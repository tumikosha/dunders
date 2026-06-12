"""Small in-desktop modal for confirming Replace All in the editor."""

from __future__ import annotations

from typing import Callable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.widget import Widget
from textual.widgets import Static

from dunders.fm.dialogs import DialogButton, FocusChainMixin
from dunders.windowing.content import WindowContent
from dunders.windowing.helpers import ModalWindow


class ReplaceAllDialog(FocusChainMixin, Container, WindowContent):
    """Yes/Cancel dialog that lives inside a `ModalWindow` on the Desktop.

    Renders as a small floating window. Tab / Left / Right cycle between
    the focusable Yes / Cancel buttons; Y / C / N / Esc are global hotkey
    shortcuts. Enter on a focused button presses it.
    """

    can_focus = False

    BINDINGS = [
        Binding("y", "confirm", show=False),
        Binding("c", "cancel", show=False),
        Binding("n", "cancel", show=False),
        Binding("escape", "cancel", show=False),
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
    """

    def __init__(self, count: int, callback: Callable[[bool], None]) -> None:
        super().__init__()
        self._count = count
        self._callback = callback
        self._done = False
        self.window_title = "Replace All"

    def compose(self) -> ComposeResult:
        word = "occurrence" if self._count == 1 else "occurrences"
        yield Static(f"{self._count} {word}", id="ra-count")
        yield Static("Replace every match?", id="ra-question")
        with Horizontal(id="ra-buttons"):
            yield DialogButton("[u]Y[/u]es", id="ra-yes")
            yield DialogButton("[u]C[/u]ancel", id="ra-no")

    def on_mount(self) -> None:
        self.call_after_refresh(self._focus_default)

    def _focus_default(self) -> None:
        try:
            self.query_one("#ra-yes", DialogButton).focus()
        except Exception:
            pass

    def _focusables(self) -> list[Widget]:
        try:
            return [
                self.query_one("#ra-yes", DialogButton),
                self.query_one("#ra-no", DialogButton),
            ]
        except Exception:
            return []

    def on_dialog_button_pressed(self, event: "DialogButton.Pressed") -> None:
        event.stop()
        if event.button.id == "ra-yes":
            self.action_confirm()
        elif event.button.id == "ra-no":
            self.action_cancel()

    def action_confirm(self) -> None:
        self._finish(True)

    def action_cancel(self) -> None:
        self._finish(False)

    def _finish(self, confirmed: bool) -> None:
        if self._done:
            return
        self._done = True
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
        # Desktop.remove_window pops the modal off `_modal_stack` and clears
        # the dim-overlay tags itself — no need to do it inline anymore.
        desktop.remove_window(node)
