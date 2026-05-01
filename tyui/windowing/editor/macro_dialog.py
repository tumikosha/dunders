"""Macro assignment dialog for windowing."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.events import Key
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Static, Checkbox


class MacroAssignDialog(ModalScreen):
    """Modal dialog for assigning a hotkey to a recorded macro."""

    DEFAULT_CSS = """
    MacroAssignDialog {
        align: center middle;
    }
    #macro-dialog {
        width: 55;
        height: 15;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #macro-dialog Static {
        width: 100%;
        content-align: center middle;
    }
    #macro-title {
        color: $text;
        text-style: bold;
    }
    #macro-info {
        color: $text-muted;
        margin: 1 0;
    }
    #macro-key-display {
        color: $accent;
        text-style: bold;
        margin: 1 0;
    }
    #macro-hint {
        color: $text-muted;
        margin: 1 0;
    }
    #macro-supported {
        color: $text-disabled;
    }
    """

    class MacroAssigned(Message):
        def __init__(self, key: str, permanent: bool) -> None:
            super().__init__()
            self.key = key
            self.permanent = permanent

    class MacroCancelled(Message):
        pass

    def __init__(self, action_count: int, **kwargs) -> None:
        super().__init__(**kwargs)
        self._action_count = action_count
        self._captured_key: str | None = None
        self._permanent = False

    def compose(self) -> ComposeResult:
        with Vertical(id="macro-dialog"):
            yield Static(f"Macro recorded ({self._action_count} actions)", id="macro-title")
            yield Static("Press desired hotkey combination:", id="macro-info")
            yield Static("waiting...", id="macro-key-display")
            yield Static("Esc to cancel, Enter to confirm", id="macro-hint")
            yield Static("F2-F12, ctrl+letter, alt+letter", id="macro-supported")
            yield Checkbox("Save permanently", id="macro-permanent")

    def on_key(self, event: Key) -> None:
        event.prevent_default()
        event.stop()

        if event.key == "escape":
            self.dismiss(None)
            return

        if event.key == "enter" and self._captured_key:
            checkbox = self.query_one("#macro-permanent", Checkbox)
            self._permanent = checkbox.value
            self.dismiss((self._captured_key, self._permanent))
            return

        if event.key in ("shift", "ctrl", "alt", "meta"):
            return

        self._captured_key = event.key
        display = self.query_one("#macro-key-display", Static)
        display.update(f"[ {event.key} ]")
