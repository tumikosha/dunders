"""Modal dialog content classes used by Phase 3 file operations.

Each dialog is a WindowContent that renders a small panel and posts a
Result message when the user makes a decision. The host app is responsible
for putting the dialog inside a ModalWindow (e.g. via show_modal) and for
closing the window after Result is received.
"""

from __future__ import annotations

import threading

from rich.segment import Segment
from rich.style import Style as RichStyle
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.message import Message
from textual.strip import Strip
from textual.widget import Widget
from textual.widgets import Input, Static

from tyui.windowing.content import WindowContent


__all__ = [
    "ConfirmDialog",
    "CopyMoveDialog",
    "InputDialog",
    "NewFileDialog",
    "ProgressDialog",
    "ShadowButton",
]


class ShadowButton(Widget):
    """Turbo Vision-style two-row button with an offset drop shadow.

    Row 0 is the button face — `  <label>  ` with a bright background.
    Row 1 is the shadow shifted one cell to the right, drawn with
    a fill character on a darker color. The widget claims a width of
    ``len(face) + 1`` so the shadow tail has a column to live in.

    Click on the face row, or Enter/Space when focused, posts
    :class:`ShadowButton.Pressed`.
    """

    DEFAULT_CSS = """
    ShadowButton {
        width: auto;
        height: 2;
        margin: 0 1 0 0;
    }
    """

    can_focus = True

    BINDINGS = [
        Binding("enter", "press", show=False),
        Binding("space", "press", show=False),
    ]

    class Pressed(Message):
        def __init__(self, button: "ShadowButton") -> None:
            self.button = button
            super().__init__()

    def __init__(
        self,
        label: str,
        *,
        id: str | None = None,
        face_bg: str = "rgb(0,160,176)",
        face_fg: str = "rgb(255,255,255)",
        shadow_char: str = "░",
        shadow_color: str = "rgb(20,20,20)",
    ) -> None:
        super().__init__(id=id)
        self.label = label
        self._face_bg = face_bg
        self._face_fg = face_fg
        self._shadow_char = shadow_char
        self._shadow_color = shadow_color

    @property
    def _face(self) -> str:
        return f"  {self.label}  "

    def get_content_width(self, container, viewport) -> int:
        # +1 column so the shadow has somewhere to extend on the right.
        return len(self._face) + 1

    def get_content_height(self, container, viewport, width) -> int:
        return 2

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        if width <= 0:
            return Strip.blank(0)
        face = self._face
        face_w = len(face)
        if y == 0:
            face_style = RichStyle(
                color=self._face_fg,
                bgcolor=self._face_bg,
                bold=True,
                reverse=self.has_focus,
            )
            tail_w = max(0, width - face_w)
            return Strip([
                Segment(face, face_style),
                Segment(" " * tail_w),
            ])
        if y == 1:
            shadow_style = RichStyle(color=self._shadow_color)
            tail_w = max(0, width - 1 - face_w)
            return Strip([
                Segment(" "),
                Segment(self._shadow_char * face_w, shadow_style),
                Segment(" " * tail_w),
            ])
        return Strip.blank(width)

    def on_click(self, event) -> None:
        if event.y == 0 and 0 <= event.x < len(self._face):
            event.stop()
            self.action_press()

    def action_press(self) -> None:
        self.post_message(ShadowButton.Pressed(self))


class ConfirmDialog(Container, WindowContent):
    """Yes/No confirmation with clickable shadow buttons.

    Y/Enter confirms, N/Esc cancels — keyboard bindings bubble up from
    inner buttons so the hotkeys still work no matter what is focused.
    """

    can_focus = True

    BINDINGS = [
        Binding("y", "confirm", show=False),
        Binding("enter", "confirm", show=False),
        Binding("n", "cancel", show=False),
        Binding("escape", "cancel", show=False),
    ]

    DEFAULT_CSS = """
    ConfirmDialog {
        layout: vertical;
    }
    ConfirmDialog #cd-prompt {
        margin: 1 1 0 1;
    }
    ConfirmDialog #cd-buttons {
        height: 2;
        align: center middle;
        margin-top: 1;
    }
    """

    class Result(Message):
        def __init__(self, dialog: "ConfirmDialog", confirmed: bool) -> None:
            self.dialog = dialog
            self.confirmed = confirmed
            super().__init__()

    def __init__(self, prompt: str, *, context: object | None = None) -> None:
        super().__init__()
        self.prompt = prompt
        # Caller-supplied payload (e.g. a DeleteRequest dataclass) so the
        # App's on_confirm_dialog_result can dispatch by isinstance.
        self.context = context
        self.window_title = "Confirm"

    def compose(self) -> ComposeResult:
        yield Static(self.prompt, id="cd-prompt")
        with Horizontal(id="cd-buttons"):
            yield ShadowButton("Yes", id="cd-yes", face_bg="rgb(0,160,90)")
            yield ShadowButton("No", id="cd-no", face_bg="rgb(160,40,40)")

    def on_shadow_button_pressed(self, event: "ShadowButton.Pressed") -> None:
        event.stop()
        if event.button.id == "cd-yes":
            self.action_confirm()
        elif event.button.id == "cd-no":
            self.action_cancel()

    def action_confirm(self) -> None:
        self.post_message(ConfirmDialog.Result(self, True))

    def action_cancel(self) -> None:
        self.post_message(ConfirmDialog.Result(self, False))


class InputDialog(Container, WindowContent):
    """Single-line text-input modal. Enter submits, Esc cancels.

    Note on multiple inheritance: WindowContent is a Widget; Container is
    also a Widget. We need Container so we can yield the Input widget from
    compose(). The WindowContent mixin gives us the title/dirty plumbing.
    """

    can_focus = False  # the inner Input takes focus; the dialog itself is a host

    DEFAULT_CSS = """
    InputDialog {
        layout: vertical;
    }
    InputDialog Input {
        margin: 1 1;
        height: 1;
        padding: 0 1;
        border: none;
        background: $boost;
        color: $text;
    }
    InputDialog Input:focus {
        background: $accent;
        color: $text;
        border: none;
    }
    """

    class Submitted(Message):
        def __init__(self, dialog: "InputDialog", value: str) -> None:
            self.dialog = dialog
            self.value = value
            super().__init__()

    class Cancelled(Message):
        def __init__(self, dialog: "InputDialog") -> None:
            self.dialog = dialog
            super().__init__()

    def __init__(
        self,
        prompt: str,
        *,
        initial: str = "",
        context: object | None = None,
    ) -> None:
        super().__init__()
        self.prompt = prompt
        self._initial = initial
        # Caller-supplied payload — same idea as ConfirmDialog.context.
        self.context = context
        self.window_title = prompt
        self._input = Input(id="input-dialog-input")

    def compose(self) -> ComposeResult:
        yield self._input

    def on_mount(self) -> None:
        if self._initial:
            self._input.value = self._initial

    # --- API used by the app shell + tests -------------------------------

    def get_value(self) -> str:
        return self._input.value

    def set_value(self, value: str) -> None:
        self._input.value = value

    def focus_input(self) -> None:
        self._input.focus()

    def action_submit(self) -> None:
        self.post_message(InputDialog.Submitted(self, self._input.value))

    def action_cancel(self) -> None:
        self.post_message(InputDialog.Cancelled(self))

    # --- key routing ------------------------------------------------------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.action_submit()

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.action_cancel()


class CopyMoveDialog(Container, WindowContent):
    """Confirm copy/move with editable destination path and clickable buttons.

    Single-target operations let the user rename the file by editing the
    destination path; multi-target operations only accept a destination
    directory (the trailing path component is ignored unless it points at a
    directory). Enter / OK button submits, Esc / Cancel button cancels.
    """

    can_focus = False  # the inner Input takes focus

    DEFAULT_CSS = """
    CopyMoveDialog {
        layout: vertical;
    }
    CopyMoveDialog #cm-prompt {
        margin: 0 1;
    }
    CopyMoveDialog #cm-input {
        margin: 0 1;
        height: 1;
        padding: 0 1;
        border: none;
        background: $boost;
    }
    CopyMoveDialog #cm-input:focus {
        background: $accent;
        color: $text;
        border: none;
    }
    CopyMoveDialog #cm-buttons {
        height: 2;
        align: center middle;
        margin-top: 1;
    }
    """

    class Submitted(Message):
        def __init__(self, dialog: "CopyMoveDialog", value: str) -> None:
            self.dialog = dialog
            self.value = value
            super().__init__()

    class Cancelled(Message):
        def __init__(self, dialog: "CopyMoveDialog") -> None:
            self.dialog = dialog
            super().__init__()

    def __init__(
        self,
        prompt: str,
        *,
        initial: str = "",
        ok_label: str = "OK",
        title: str = "Copy",
        context: object | None = None,
    ) -> None:
        super().__init__()
        self.prompt = prompt
        self._initial = initial
        self._ok_label = ok_label
        self.context = context
        self.window_title = title
        self._input = Input(value=initial, id="cm-input")

    def compose(self) -> ComposeResult:
        yield Static(self.prompt, id="cm-prompt")
        yield self._input
        with Horizontal(id="cm-buttons"):
            yield ShadowButton(
                self._ok_label,
                id="cm-ok",
                face_bg="rgb(0,160,90)",
            )
            yield ShadowButton(
                "Cancel",
                id="cm-cancel",
                face_bg="rgb(160,40,40)",
            )

    def get_value(self) -> str:
        return self._input.value

    def set_value(self, value: str) -> None:
        self._input.value = value

    def focus_input(self) -> None:
        self._input.focus()

    def action_submit(self) -> None:
        self.post_message(CopyMoveDialog.Submitted(self, self._input.value))

    def action_cancel(self) -> None:
        self.post_message(CopyMoveDialog.Cancelled(self))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.action_submit()

    def on_shadow_button_pressed(self, event: "ShadowButton.Pressed") -> None:
        event.stop()
        if event.button.id == "cm-ok":
            self.action_submit()
        elif event.button.id == "cm-cancel":
            self.action_cancel()

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.action_cancel()


class NewFileDialog(Container, WindowContent):
    """Modal "New file" prompt: borderless single-line input + Create/Cancel.

    The input is rendered with a flat $boost background (no border) and
    Create/Cancel are :class:`ShadowButton` instances so the dialog matches
    the Turbo-Vision-style copy/move modal.
    """

    can_focus = False  # the inner Input takes focus

    DEFAULT_CSS = """
    NewFileDialog {
        layout: vertical;
    }
    NewFileDialog #nf-prompt {
        margin: 0 1;
    }
    NewFileDialog #nf-input {
        margin: 0 1;
        height: 1;
        padding: 0 1;
        border: none;
        background: $boost;
    }
    NewFileDialog #nf-input:focus {
        background: $accent;
        color: $text;
        border: none;
    }
    NewFileDialog #nf-buttons {
        height: 2;
        align: center middle;
        margin-top: 1;
    }
    """

    class Submitted(Message):
        def __init__(self, dialog: "NewFileDialog", value: str) -> None:
            self.dialog = dialog
            self.value = value
            super().__init__()

    class Cancelled(Message):
        def __init__(self, dialog: "NewFileDialog") -> None:
            self.dialog = dialog
            super().__init__()

    def __init__(
        self,
        prompt: str,
        *,
        context: object | None = None,
        submit_label: str = "Create",
        title: str = "New",
        initial: str = "",
    ) -> None:
        super().__init__()
        self.prompt = prompt
        self.context = context
        self.window_title = title
        self._submit_label = submit_label
        self._initial = initial
        self._input = Input(id="nf-input")

    def compose(self) -> ComposeResult:
        yield Static(self.prompt, id="nf-prompt")
        yield self._input
        with Horizontal(id="nf-buttons"):
            yield ShadowButton(self._submit_label, id="nf-create", face_bg="rgb(0,160,90)")
            yield ShadowButton("Cancel", id="nf-cancel", face_bg="rgb(160,40,40)")

    def on_mount(self) -> None:
        if self._initial:
            self._input.value = self._initial

    def get_value(self) -> str:
        return self._input.value

    def focus_input(self) -> None:
        self._input.focus()

    def action_submit(self) -> None:
        self.post_message(NewFileDialog.Submitted(self, self._input.value))

    def action_cancel(self) -> None:
        self.post_message(NewFileDialog.Cancelled(self))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.action_submit()

    def on_shadow_button_pressed(self, event: "ShadowButton.Pressed") -> None:
        event.stop()
        if event.button.id == "nf-create":
            self.action_submit()
        elif event.button.id == "nf-cancel":
            self.action_cancel()

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.action_cancel()


class ProgressDialog(WindowContent):
    """Progress modal: title + N/total counter + cancel.

    The action helper running on a worker thread reads `cancel_event` to
    know if it should stop. Pressing `c` or Esc inside the dialog sets the
    event; the worker checks between items and reports `cancelled=True`.
    """

    can_focus = True

    BINDINGS = [
        Binding("c", "cancel", show=False),
        Binding("escape", "cancel", show=False),
    ]

    def __init__(self, title: str, total: int) -> None:
        super().__init__()
        self.title_text = title
        self.window_title = title
        self.total = total
        self.current = 0
        self.cancel_event = threading.Event()

    def set_progress(self, current: int, total: int) -> None:
        self.current = current
        self.total = total
        self.refresh()

    # Cancel button render layout. _CANCEL_LABEL is the clickable text;
    # _CANCEL_X is its starting column inside the dialog content area.
    _CANCEL_LABEL = "[C] Cancel"
    _CANCEL_X = 2
    _CANCEL_Y = 3

    _BAR_FILLED = "█"
    _BAR_EMPTY = "░"

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        if width <= 0:
            return Strip.blank(0)
        if y == 0:
            text = (" " + self.title_text).ljust(width)[:width]
            return Strip([Segment(text, RichStyle(bold=True))])
        if y == 1:
            return self._render_bar(width)
        if y == self._CANCEL_Y:
            pad = " " * self._CANCEL_X
            text = (pad + self._CANCEL_LABEL + "  ").ljust(width)[:width]
            return Strip([Segment(text, RichStyle(bold=True))])
        return Strip([Segment(" " * width)])

    def _render_bar(self, width: int) -> Strip:
        counter = f" {self.current} / {self.total}"
        # 4 chars padding (2 each side), 2 chars for "[]" — leave the rest
        # for the bar plus the counter suffix.
        budget = max(1, width - 4 - 2 - len(counter))
        bar_width = max(1, budget)
        if self.total > 0:
            ratio = max(0.0, min(1.0, self.current / self.total))
        else:
            ratio = 0.0
        filled = int(ratio * bar_width)
        bar = self._BAR_FILLED * filled + self._BAR_EMPTY * (bar_width - filled)
        text = f"  [{bar}]{counter}".ljust(width)[:width]
        return Strip([Segment(text)])

    def on_click(self, event) -> None:
        """Mouse cancel: click anywhere on the [C] Cancel row triggers cancel."""
        if getattr(event, "y", -1) != self._CANCEL_Y:
            return
        x = getattr(event, "x", -1)
        if self._CANCEL_X <= x < self._CANCEL_X + len(self._CANCEL_LABEL):
            event.stop()
            self.action_cancel()

    def action_cancel(self) -> None:
        self.cancel_event.set()
