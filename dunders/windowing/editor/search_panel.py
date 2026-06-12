from __future__ import annotations

from dataclasses import replace
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, Label

from dunders.windowing.core.search import SearchOptions


class SearchPanel(Widget):
    """Inline find/replace panel mounted under EditorContent."""

    DEFAULT_CSS = """
    SearchPanel { display: none; height: 2; background: $panel; }
    SearchPanel Horizontal { height: 1; }
    SearchPanel Input {
        width: 30;
        margin: 0 1;
        height: 1;
        padding: 0 1;
        border: none;
        background: black;
        color: white;
    }
    SearchPanel Input:focus {
        background: $accent;
        color: white;
        border: none;
    }
    SearchPanel Label.flag { width: auto; padding: 0 1; border: none; }
    SearchPanel Label.flag.-on { background: $boost; }
    SearchPanel Label.flag:focus { background: $accent; border: none; }
    SearchPanel Label#status { width: auto; padding: 0 1; border: none; }
    SearchPanel Label#status:focus { background: $accent; border: none; }
    SearchPanel Label.btn {
        width: auto;
        padding: 0 1;
        margin: 0 0 0 1;
        background: $boost;
        color: $text;
        border: none;
    }
    SearchPanel Label.btn:hover { background: $accent; }
    SearchPanel Label.btn:focus { background: $accent; color: $text; border: none; }
    SearchPanel Label.btn-close {
        width: auto;
        padding: 0 1;
        margin: 0;
        background: $boost;
        color: $text;
        dock: right;
        border: none;
    }
    SearchPanel Label.btn-close:hover { background: red; color: white; }
    SearchPanel Label.btn-close:focus { background: red; color: white; border: none; }
    """

    class PatternChanged(Message):
        def __init__(self, pattern: str, options: SearchOptions) -> None:
            super().__init__()
            self.pattern = pattern
            self.options = options

    class FindNext(Message):
        pass

    class FindPrev(Message):
        pass

    class ReplaceOne(Message):
        def __init__(self, replacement: str) -> None:
            super().__init__()
            self.replacement = replacement

    class ReplaceAll(Message):
        def __init__(self, replacement: str) -> None:
            super().__init__()
            self.replacement = replacement

    class Closed(Message):
        pass

    FLAG_LABELS = [
        ("case_sensitive", "Aa"),
        ("whole_word", r"\b"),
        ("regex", ".*"),
        ("wrap_around", "↺"),
        ("in_selection", "sel"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.display = False
        self.mode: str = "find"
        self.options = SearchOptions()
        self.find_input: Input | None = None
        self.replace_input: Input | None = None
        self._status: Label | None = None
        self._flag_labels: dict[str, Label] = {}
        self._btn_search: Label | None = None
        self._btn_close: Label | None = None
        self._btn_replace: Label | None = None
        self._btn_replace_all: Label | None = None

    def compose(self) -> ComposeResult:
        with Vertical():
            with Horizontal(classes="row-find"):
                yield Label("Find: ")
                self.find_input = Input(id="find-input", select_on_focus=False)
                yield self.find_input
                for name, glyph in self.FLAG_LABELS:
                    lbl = Label(self._flag_text(name, glyph), classes="flag", id=f"flag-{name}")
                    lbl.can_focus = True
                    self._flag_labels[name] = lbl
                    yield lbl
                self._status = Label("—", id="status")
                self._status.can_focus = True
                yield self._status
                self._btn_search = Label("Search", classes="btn", id="btn-search")
                self._btn_search.can_focus = True
                yield self._btn_search
                self._btn_close = Label(" ✕ ", classes="btn-close", id="btn-close")
                self._btn_close.can_focus = True
                yield self._btn_close
            with Horizontal(classes="row-replace"):
                yield Label("Replace: ")
                self.replace_input = Input(id="replace-input", select_on_focus=False)
                yield self.replace_input
                self._btn_replace = Label("Replace", classes="btn", id="btn-replace")
                self._btn_replace.can_focus = True
                yield self._btn_replace
                self._btn_replace_all = Label("Replace All", classes="btn", id="btn-replace-all")
                self._btn_replace_all.can_focus = True
                yield self._btn_replace_all

    def _flag_text(self, name: str, glyph: str) -> str:
        on_ = getattr(self.options, name)
        return f"[{'x' if on_ else ' '}]{glyph}"

    def _refresh_flags(self) -> None:
        for name, glyph in self.FLAG_LABELS:
            lbl = self._flag_labels[name]
            lbl.update(self._flag_text(name, glyph))
            lbl.set_class(getattr(self.options, name), "-on")

    def show_find(self) -> None:
        self._show("find")

    def show_replace(self) -> None:
        self._show("replace")

    def _show(self, mode: str) -> None:
        self.mode = mode
        self.display = True
        self.set_class(mode == "find", "-find")
        self.set_class(mode == "replace", "-replace")
        # Reset state — clean start (per spec)
        self.options = SearchOptions()
        self._refresh_flags()
        if self.find_input is not None:
            self.find_input.value = ""
        if self.replace_input is not None:
            self.replace_input.value = ""
        if self._status is not None:
            self._status.update("—")
        if self.find_input is not None:
            self.find_input.focus()

    def close(self) -> None:
        self.display = False
        self.post_message(self.Closed())

    def set_status(self, current: int, total: int, *, error: str | None = None) -> None:
        if self._status is None:
            return
        if error is not None:
            self._status.update(f"[red]{error}[/]")
        elif total == 0:
            self._status.update("[red]no match[/]")
        else:
            shown = current + 1 if current >= 0 else 0
            self._status.update(f"{shown}/{total}")

    def _toggle_flag(self, name: str) -> None:
        self.options = replace(self.options, **{name: not getattr(self.options, name)})
        self._refresh_flags()
        self._emit_pattern_changed()

    def _activate(self, target) -> bool:
        """Trigger the action associated with a focusable label.

        Returns True if target matched and an action was dispatched.
        """
        if target is None:
            return False
        for name, _glyph in self.FLAG_LABELS:
            if target is self._flag_labels.get(name):
                self._toggle_flag(name)
                return True
        if target is self._btn_search:
            self.post_message(self.FindNext())
            return True
        if target is self._btn_close:
            self.close()
            return True
        if target is self._btn_replace and self.replace_input is not None:
            self.post_message(self.ReplaceOne(self.replace_input.value))
            return True
        if target is self._btn_replace_all and self.replace_input is not None:
            self.post_message(self.ReplaceAll(self.replace_input.value))
            return True
        return False

    def on_click(self, event) -> None:
        if self._activate(getattr(event, "widget", None)):
            event.stop()

    def on_key(self, event) -> None:
        if event.key not in ("enter", "space"):
            return
        if self._activate(self.screen.focused if self.screen is not None else None):
            event.stop()

    def post_message(self, message):
        handler = getattr(self, "post_message_handler", None)
        if handler is not None:
            handler(message)
        return super().post_message(message)

    def _emit_pattern_changed(self) -> None:
        if self.find_input is None:
            return
        self.post_message(self.PatternChanged(self.find_input.value, self.options))

    @on(Input.Changed, "#find-input")
    def _on_find_changed(self, event: Input.Changed) -> None:
        self._emit_pattern_changed()

    @on(Input.Submitted, "#find-input")
    def _on_find_submitted(self, event: Input.Submitted) -> None:
        self.post_message(self.FindNext())

    @on(Input.Submitted, "#replace-input")
    def _on_replace_submitted(self, event: Input.Submitted) -> None:
        if self.replace_input is not None:
            self.post_message(self.ReplaceOne(self.replace_input.value))
