"""CommandLine — single-line input docked above the StatusBar.

Phase 1: emits a Submitted message on Enter; no execution. Phase 5 will wire
this up to a CommandRunner that captures shell output into a window.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container
from textual.message import Message
from textual.widgets import Input


class CommandLine(Container):
    """A docked one-line shell-style input."""

    DEFAULT_CSS = """
    CommandLine {
        dock: bottom;
        height: 1;
        layer: overlay;
    }
    CommandLine Input {
        border: none;
        padding: 0 1;
        height: 1;
    }
    """

    class Submitted(Message):
        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()

    def __init__(self, id: str | None = None) -> None:
        super().__init__(id=id)
        self._input = Input(placeholder="$ command", id="cmdline-input")

    def compose(self) -> ComposeResult:
        yield self._input

    # --- API used by tests and the app shell ------------------------------

    @property
    def text(self) -> str:
        return self._input.value

    def set_text(self, value: str) -> None:
        self._input.value = value

    def submit(self) -> None:
        text = self._input.value
        self._input.value = ""
        self.post_message(CommandLine.Submitted(text))

    # --- Textual event hook ------------------------------------------------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.submit()
