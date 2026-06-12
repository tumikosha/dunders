"""Demo WindowContent implementations used by the showcase app."""

from __future__ import annotations

from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, Input, Label, ListView, ListItem, Log, TextArea

from dunders.windowing.content import WindowContent, WindowCommand


class LabelContent(WindowContent):
    """Static multi-line text."""

    def __init__(self, text: str, title: str | None = None) -> None:
        super().__init__()
        self._text = text
        if title is not None:
            self.window_title = title

    def compose(self) -> ComposeResult:
        yield Label(self._text)


class TextAreaContent(WindowContent):
    """A multi-line editor that marks the window dirty on changes."""

    DEFAULT_CSS = """
    TextAreaContent { background: transparent; }
    TextAreaContent TextArea { border: none; padding: 0; background: transparent; }
    TextAreaContent TextArea:focus { border: none; background: transparent; }
    TextAreaContent TextArea > .text-area--cursor-line { background: transparent; }
    """

    def __init__(self, initial: str = "", title: str | None = None) -> None:
        super().__init__()
        self._initial = initial
        if title is not None:
            self.window_title = title

    def compose(self) -> ComposeResult:
        ta = TextArea(self._initial, id="ta")
        yield ta

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        self.is_dirty = True


class ListContent(WindowContent):
    """Scrollable list of items."""

    DEFAULT_CSS = """
    ListContent { background: transparent; }
    ListContent ListView { border: none; background: transparent; }
    ListContent ListView:focus { border: none; background: transparent; }
    ListContent ListItem { background: transparent; }
    """

    def __init__(self, items: list[str], title: str | None = None) -> None:
        super().__init__()
        self._items = items
        if title is not None:
            self.window_title = title

    def compose(self) -> ComposeResult:
        yield ListView(*[ListItem(Label(i)) for i in self._items])


class LogContent(WindowContent):
    """Auto-scrolling log; subtitle shows line count."""

    DEFAULT_CSS = """
    LogContent { background: transparent; }
    LogContent Log { border: none; background: transparent; }
    LogContent Log:focus { border: none; background: transparent; }
    """

    def __init__(self, title: str | None = None) -> None:
        super().__init__()
        if title is not None:
            self.window_title = title
        self._count = 0

    def compose(self) -> ComposeResult:
        yield Log(auto_scroll=True, id="log")

    def append(self, line: str) -> None:
        log = self.query_one("#log", Log)
        log.write_line(line)
        self._count += 1
        self.window_subtitle = f"{self._count} lines"


class FormContent(WindowContent):
    """Two inputs + OK button; demonstrates get_commands."""

    def __init__(self, title: str | None = None) -> None:
        super().__init__()
        if title is not None:
            self.window_title = title

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("Name:"),
            Input(placeholder="your name", id="name"),
            Label("Email:"),
            Input(placeholder="you@example.com", id="email"),
            Button("Submit", id="submit"),
        )

    def get_commands(self) -> list[WindowCommand]:
        return [
            WindowCommand(id="submit", label="Submit", handler=self._submit, hotkey="ctrl+enter"),
        ]

    def _submit(self) -> None:
        self.is_dirty = False
