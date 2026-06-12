"""Modal dialog content classes used by Phase 3 file operations.

Each dialog is a WindowContent that renders a small panel and posts a
Result message when the user makes a decision. The host app is responsible
for putting the dialog inside a ModalWindow (e.g. via show_modal) and for
closing the window after Result is received.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from rich.segment import Segment
from rich.style import Style as RichStyle
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.message import Message
from textual.strip import Strip
from textual import events
from textual.widget import Widget
from textual.widgets import Input, Static

from dunders.windowing.content import WindowContent

if TYPE_CHECKING:
    from dunders.fm.find_file import FindOptions


__all__ = [
    "ChangeAttributesDialog",
    "ConfirmDialog",
    "CopyMoveDialog",
    "FindFileDialog",
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
        hotkey: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self.label = label
        self._face_bg = face_bg
        self._face_fg = face_fg
        self._shadow_char = shadow_char
        self._shadow_color = shadow_color
        # Hotkey letter (case-insensitive). If set, render_line underlines
        # the first matching character in the label so users see which
        # key activates the button. The dialog hosting the button is
        # responsible for binding that letter to the action — the button
        # itself does NOT register a binding (it would only fire when the
        # button is focused, which defeats the point of a hotkey).
        self.hotkey = hotkey.lower() if hotkey else None

    @property
    def _face(self) -> str:
        return f"  {self.label}  "

    def _hotkey_index(self) -> int:
        """Index of the hotkey character inside `_face`, or -1."""
        if not self.hotkey:
            return -1
        face_lower = self._face.lower()
        return face_lower.find(self.hotkey)

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
            hk = self._hotkey_index()
            tail_w = max(0, width - face_w)
            if hk < 0:
                return Strip([
                    Segment(face, face_style),
                    Segment(" " * tail_w),
                ])
            # Split face into [pre][hot][post] so the hotkey char gets
            # an extra `underline` style on top of the face style.
            hot_style = face_style + RichStyle(underline=True)
            return Strip([
                Segment(face[:hk], face_style),
                Segment(face[hk], hot_style),
                Segment(face[hk + 1:], face_style),
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


class FocusChainMixin:
    """Keyboard navigation mixin for modal dialogs.

    Provides a uniform Tab / Shift+Tab / Left / Right focus chain across
    a subclass-provided list of focusable widgets. Originally lifted from
    `ChangeAttributesDialog`. Up/Down are deliberately NOT included — they
    would clash with `Input` cursor-line navigation; dialogs that need
    Up/Down (e.g. perm-checkbox columns) declare them in their own
    `BINDINGS`.

    Subclass contract:
        - Override `_focusables()` to return widgets in tab order.
        - Place the mixin BEFORE `Container` in the bases so the
          `__init_subclass__` runs before the DOMNode chain freezes.

    Implementation note: Textual collects `BINDINGS` only from classes
    inheriting `DOMNode`, so a plain Python mixin's `BINDINGS` are
    invisible to the binding registry. To work around that we splice
    the nav bindings into the concrete subclass's `BINDINGS` via
    `__init_subclass__` — the subclass IS a DOMNode, so its BINDINGS
    are honoured.
    """

    NAV_BINDINGS = [
        Binding("tab",       "focus_next",        show=False),
        Binding("shift+tab", "focus_prev",        show=False),
        Binding("left",      "select_button(-1)", show=False),
        Binding("right",     "select_button(1)",  show=False),
    ]

    def __init_subclass__(cls, **kwargs) -> None:
        # Splice nav bindings BEFORE delegating up the chain — DOMNode's
        # __init_subclass__ snapshots BINDINGS into its registry, so we
        # must mutate cls.BINDINGS first.
        own = list(getattr(cls, "BINDINGS", []) or [])
        own_keys = {b.key for b in own if hasattr(b, "key")}
        cls.BINDINGS = own + [
            b for b in FocusChainMixin.NAV_BINDINGS if b.key not in own_keys
        ]
        super().__init_subclass__(**kwargs)

    def _focusables(self) -> list[Widget]:
        return []

    def _current_focus_index(self) -> int:
        focused = self.app.focused if self.app is not None else None
        for i, w in enumerate(self._focusables()):
            if w is focused:
                return i
        return -1

    def _move_focus(self, delta: int) -> None:
        chain = self._focusables()
        if not chain:
            return
        idx = self._current_focus_index()
        if idx == -1:
            chain[0].focus()
            return
        chain[(idx + delta) % len(chain)].focus()

    def action_focus_next(self) -> None:
        self._move_focus(1)

    def action_focus_prev(self) -> None:
        self._move_focus(-1)

    def action_select_button(self, delta: int) -> None:
        # Left/Right only swap between buttons. If focus is on an Input
        # (or anything that isn't a ShadowButton/DialogButton), no-op so
        # cursor movement inside the Input keeps working.
        focused = self.app.focused if self.app is not None else None
        if not isinstance(focused, (ShadowButton, DialogButton)):
            return
        chain = self._focusables()
        idx = self._current_focus_index()
        if idx == -1 or not chain:
            return
        n = len(chain)
        # Walk in `delta` direction until we hit the next focusable that
        # is also a button. Stops if we wrap fully.
        for step in range(1, n + 1):
            cand = chain[(idx + delta * step) % n]
            if isinstance(cand, (ShadowButton, DialogButton)):
                cand.focus()
                return


class ConfirmDialog(FocusChainMixin, Container, WindowContent):
    """Yes/No confirmation with clickable shadow buttons.

    Y/Enter confirms, N/Esc cancels — keyboard bindings bubble up from
    inner buttons so the hotkeys still work no matter what is focused.
    """

    # Dialog itself is not focusable — keyboard focus belongs to one of
    # the ShadowButtons. If the container were also focusable, Textual's
    # default Tab traversal would land on it AFTER our FocusChainMixin
    # cycled the buttons, breaking the wrap from No → Yes.
    can_focus = False

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
            yield ShadowButton(
                "Yes", id="cd-yes", face_bg="rgb(0,160,90)", hotkey="y"
            )
            yield ShadowButton(
                "No", id="cd-no", face_bg="rgb(160,40,40)", hotkey="n"
            )

    def on_mount(self) -> None:
        self.call_after_refresh(self._focus_default)

    def _focus_default(self) -> None:
        try:
            self.query_one("#cd-yes", ShadowButton).focus()
        except Exception:
            pass

    def _focusables(self) -> list[Widget]:
        try:
            return [
                self.query_one("#cd-yes", ShadowButton),
                self.query_one("#cd-no", ShadowButton),
            ]
        except Exception:
            return []

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


class CopyMoveDialog(FocusChainMixin, Container, WindowContent):
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
        ok_hotkey: str | None = None,
        title: str = "Copy",
        context: object | None = None,
    ) -> None:
        super().__init__()
        self.prompt = prompt
        self._initial = initial
        self._ok_label = ok_label
        # Cancel always claims 'c'. If the OK label also starts with 'c'
        # (e.g. "Copy"), pick the first non-'c' alpha char so they don't
        # collide. Caller can override via ok_hotkey.
        self._ok_hotkey = (ok_hotkey or self._auto_ok_hotkey(ok_label)).lower()
        self.context = context
        self.window_title = title
        self._input = Input(value=initial, id="cm-input")

    @staticmethod
    def _auto_ok_hotkey(label: str) -> str:
        first = label[:1].lower()
        if first and first != "c":
            return first
        for ch in label[1:].lower():
            if ch.isalpha() and ch != "c":
                return ch
        return first or "o"

    def compose(self) -> ComposeResult:
        yield Static(self.prompt, id="cm-prompt")
        yield self._input
        with Horizontal(id="cm-buttons"):
            yield ShadowButton(
                self._ok_label,
                id="cm-ok",
                face_bg="rgb(0,160,90)",
                hotkey=self._ok_hotkey,
            )
            yield ShadowButton(
                "Cancel",
                id="cm-cancel",
                face_bg="rgb(160,40,40)",
                hotkey="c",
            )

    def _focusables(self) -> list[Widget]:
        try:
            return [
                self._input,
                self.query_one("#cm-ok", ShadowButton),
                self.query_one("#cm-cancel", ShadowButton),
            ]
        except Exception:
            return [self._input]

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
        key = event.key
        if key == "escape":
            event.stop()
            self.action_cancel()
            return
        # OK hotkey letter (e.g. 'o' for Copy, 'm' for Move) and 'c' for
        # Cancel. Textual's Input swallows printable keys before they
        # bubble, so these only fire when focus is NOT on the cm-input.
        if not key or len(key) != 1:
            return
        norm = key.lower()
        if norm == self._ok_hotkey:
            event.stop()
            self.action_submit()
        elif norm == "c":
            event.stop()
            self.action_cancel()


class NewFileDialog(FocusChainMixin, Container, WindowContent):
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
        submit_hotkey: str | None = None,
        title: str = "New",
        initial: str = "",
    ) -> None:
        super().__init__()
        self.prompt = prompt
        self.context = context
        self.window_title = title
        self._submit_label = submit_label
        # Reuse CopyMoveDialog's policy: Cancel claims 'c'; if the
        # submit label also starts with 'c' (e.g. "Create"), step over
        # to the next non-'c' letter — c[R]eate. Caller can override.
        self._submit_hotkey = (
            submit_hotkey or CopyMoveDialog._auto_ok_hotkey(submit_label)
        ).lower()
        self._initial = initial
        self._input = Input(id="nf-input")

    def compose(self) -> ComposeResult:
        yield Static(self.prompt, id="nf-prompt")
        yield self._input
        with Horizontal(id="nf-buttons"):
            yield ShadowButton(
                self._submit_label,
                id="nf-create",
                face_bg="rgb(0,160,90)",
                hotkey=self._submit_hotkey,
            )
            yield ShadowButton(
                "Cancel",
                id="nf-cancel",
                face_bg="rgb(160,40,40)",
                hotkey="c",
            )

    def on_mount(self) -> None:
        if self._initial:
            self._input.value = self._initial

    def _focusables(self) -> list[Widget]:
        try:
            return [
                self._input,
                self.query_one("#nf-create", ShadowButton),
                self.query_one("#nf-cancel", ShadowButton),
            ]
        except Exception:
            return [self._input]

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
        key = event.key
        if key == "escape":
            event.stop()
            self.action_cancel()
            return
        if not key or len(key) != 1:
            return
        norm = key.lower()
        if norm == self._submit_hotkey:
            event.stop()
            self.action_submit()
        elif norm == "c":
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


_CHMOD_PERMS: tuple[tuple[int, str, str], ...] = (
    # (mask, label-with-rich-underline, hotkey letter)
    (0o4000, "set [u]u[/u]ser ID on execution", "u"),
    (0o2000, "set [u]g[/u]roup ID on execution", "g"),
    (0o1000, "stick[u]y[/u] bit", "y"),
    (0o0400, "[u]r[/u]ead by owner", "r"),
    (0o0200, "[u]w[/u]rite by owner", "w"),
    (0o0100, "e[u]x[/u]ecute/search by owner", "x"),
    (0o0040, "rea[u]d[/u] by group", "d"),
    (0o0020, "write [u]b[/u]y group", "b"),
    (0o0010, "execute/sea[u]r[/u]ch by group", "r2"),
    (0o0004, "read by [u]o[/u]thers", "o"),
    (0o0002, "wr[u]i[/u]te by others", "i"),
    (0o0001, "execu[u]t[/u]e by others", "t"),
)


class DialogButton(Static):
    """Focusable Label-style button for the chmod dialog footer.

    Uses the same `Label.btn` visual style (centered text on $boost,
    accent on focus/selection) but is focusable so Tab/Arrow keys can
    move keyboard focus to it. Enter/Space posts ``Pressed``.
    """

    can_focus = True

    DEFAULT_CSS = """
    DialogButton {
        width: auto;
        padding: 0 2;
        margin: 0 1;
        background: $boost;
        color: $text;
    }
    DialogButton:hover { background: $accent; }
    DialogButton:focus {
        background: $accent;
        color: $text;
        text-style: bold;
    }
    """

    BINDINGS = [
        Binding("enter", "press", show=False),
        Binding("space", "press", show=False),
    ]

    class Pressed(Message):
        def __init__(self, button: "DialogButton") -> None:
            self.button = button
            super().__init__()

    def __init__(self, label: str, *, id: str | None = None) -> None:
        super().__init__(label, markup=True, id=id)

    def action_press(self) -> None:
        self.post_message(DialogButton.Pressed(self))

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.focus()
        self.action_press()


class _PermCheckbox(Static):
    """Single chmod permission row: '[x] label' / '[ ] label'.

    Focusable so Up/Down navigation through siblings works via standard
    Textual focus traversal. Space or Enter toggles ``checked``.
    """

    can_focus = True

    DEFAULT_CSS = """
    _PermCheckbox {
        height: 1;
        width: 100%;
        padding: 0 1;
        background: $surface;
        color: $text;
    }
    _PermCheckbox:focus {
        background: $accent;
        color: $text;
        text-style: bold;
    }
    """

    BINDINGS = [
        Binding("space", "toggle", show=False),
        Binding("enter", "toggle", show=False),
    ]

    class Toggled(Message):
        def __init__(self, box: "_PermCheckbox") -> None:
            self.box = box
            super().__init__()

    def __init__(self, *, label_markup: str, mask: int, checked: bool = False) -> None:
        mark = "x" if checked else " "
        super().__init__(f"\\[{mark}] {label_markup}", markup=True)
        self._label_markup = label_markup
        self.mask = mask
        self.checked = checked

    def _refresh_label(self) -> None:
        # Don't shadow Widget._render — it's the internal render hook that
        # MUST return a Visual. Naming this `_render` returned None and
        # crashed the renderer with `'NoneType' has no attribute 'render_strips'`.
        mark = "x" if self.checked else " "
        self.update(f"\\[{mark}] {self._label_markup}")

    def action_toggle(self) -> None:
        self.checked = not self.checked
        self._refresh_label()
        self.post_message(_PermCheckbox.Toggled(self))

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.focus()
        self.action_toggle()


class ChangeAttributesDialog(FocusChainMixin, Container, WindowContent):
    """mc-style chmod dialog: 12 permission checkboxes + file info + buttons.

    Layout mirrors Midnight Commander's "Chmod command" dialog: a left
    "Permission" group with 12 toggleable rows (setuid/setgid/sticky +
    rwx for owner/group/other), a right "File" group with read-only
    name / octal / owner / group, and a bottom row with Set / Cancel.

    Navigation: Tab/Shift+Tab/Up/Down cycle through the checkboxes and
    buttons. Left/Right swap between Set and Cancel when a button is
    focused. Space/Enter on a checkbox toggles; on a button presses.
    Esc cancels.
    """

    can_focus = False

    BINDINGS = [
        # Tab / Shift+Tab / Left / Right come from FocusChainMixin.
        # Up / Down stay here so the perm-checkbox column also responds
        # to vertical-arrow nav (the mixin omits Up/Down to avoid Input
        # cursor clashes in other dialogs).
        Binding("up", "focus_prev", show=False),
        Binding("down", "focus_next", show=False),
        Binding("escape", "cancel", show=False),
    ]

    DEFAULT_CSS = """
    ChangeAttributesDialog {
        layout: vertical;
        background: $surface;
    }
    ChangeAttributesDialog #ca-body {
        height: 1fr;
        layout: horizontal;
    }
    ChangeAttributesDialog #ca-perms {
        width: 1fr;
        height: 100%;
        border: round $accent;
        padding: 0 0;
    }
    ChangeAttributesDialog #ca-info {
        width: 32;
        height: 100%;
        border: round $accent;
        padding: 0 1;
    }
    ChangeAttributesDialog #ca-info .ca-info-label {
        color: $text-muted;
    }
    ChangeAttributesDialog #ca-info .ca-info-value {
        color: $text;
        margin-bottom: 1;
    }
    ChangeAttributesDialog #ca-buttons {
        height: 1;
        align: center middle;
        margin-top: 1;
    }
    ChangeAttributesDialog Label.btn {
        width: auto;
        padding: 0 2;
        margin: 0 1;
        background: $boost;
        color: $text;
    }
    ChangeAttributesDialog Label.btn:hover { background: $accent; }
    ChangeAttributesDialog Label.btn.-selected {
        background: $accent;
        color: $text;
        text-style: bold;
    }
    ChangeAttributesDialog Label.btn.-selected:hover { background: $accent-lighten-1; }
    """

    class Submitted(Message):
        def __init__(self, dialog: "ChangeAttributesDialog", mode: int) -> None:
            self.dialog = dialog
            self.mode = mode
            super().__init__()

    class Cancelled(Message):
        def __init__(self, dialog: "ChangeAttributesDialog") -> None:
            self.dialog = dialog
            super().__init__()

    def __init__(
        self,
        *,
        target_label: str,
        current_mode: int,
        full_st_mode: int | None = None,
        owner_name: str = "",
        group_name: str = "",
        context: object | None = None,
    ) -> None:
        super().__init__()
        self._target_label = target_label
        self._current_mode = current_mode & 0o7777
        # Permissions field shows full st_mode (file type + perms) like mc
        # does — falls back to permission bits only if caller didn't supply.
        self._display_mode = full_st_mode if full_st_mode is not None else self._current_mode
        self._owner_name = owner_name
        self._group_name = group_name
        self.context = context
        self.window_title = "Chmod command"
        self._boxes: list[_PermCheckbox] = []

    def compose(self) -> ComposeResult:
        with Container(id="ca-body"):
            with Container(id="ca-perms"):
                self._boxes = []
                for mask, label_markup, _ in _CHMOD_PERMS:
                    box = _PermCheckbox(
                        label_markup=label_markup,
                        mask=mask,
                        checked=bool(self._current_mode & mask),
                    )
                    self._boxes.append(box)
                    yield box
            with Container(id="ca-info"):
                yield Static("Name:", classes="ca-info-label")
                yield Static(self._target_label or "—", classes="ca-info-value")
                yield Static("Permissions (octal):", classes="ca-info-label")
                yield Static(f"{self._display_mode:o}", classes="ca-info-value")
                yield Static("Owner name:", classes="ca-info-label")
                yield Static(self._owner_name or "—", classes="ca-info-value")
                yield Static("Group name:", classes="ca-info-label")
                yield Static(self._group_name or "—", classes="ca-info-value")
        with Horizontal(id="ca-buttons"):
            yield DialogButton("[u]S[/u]et", id="ca-set")
            yield DialogButton("[u]C[/u]ancel", id="ca-cancel")

    def on_mount(self) -> None:
        self.call_after_refresh(self._focus_first_box)

    def _focus_first_box(self) -> None:
        if self._boxes:
            try:
                self._boxes[0].focus()
            except Exception:
                pass

    def focus_input(self) -> None:
        # Kept for API symmetry with other fm dialogs (see app.py wiring).
        self._focus_first_box()

    def _focusables(self) -> list[Widget]:
        try:
            ok = self.query_one("#ca-set", DialogButton)
            cancel = self.query_one("#ca-cancel", DialogButton)
        except Exception:
            return list(self._boxes)
        return [*self._boxes, ok, cancel]

    def on_key(self, event) -> None:
        key = event.key
        if not key or len(key) != 1:
            return
        # Lowercase so Shift+S / capslock are equivalent to plain s.
        key = key.lower()
        # Set / Cancel hotkeys — global, work from any focus.
        if key == "s":
            event.stop()
            self.action_submit()
            return
        if key == "c":
            event.stop()
            self.action_cancel()
            return
        # Per-row hotkeys: pressing the underlined letter toggles the
        # matching box and parks focus on it.
        for box, perm in zip(self._boxes, _CHMOD_PERMS):
            hotkey = perm[2]
            normalised = hotkey[0] if hotkey.endswith("2") else hotkey
            if normalised == key:
                event.stop()
                box.focus()
                box.action_toggle()
                return

    def on_dialog_button_pressed(self, event: "DialogButton.Pressed") -> None:
        event.stop()
        if event.button.id == "ca-set":
            self.action_submit()
        elif event.button.id == "ca-cancel":
            self.action_cancel()

    def _compute_mode(self) -> int:
        mode = 0
        for box in self._boxes:
            if box.checked:
                mode |= box.mask
        return mode

    def action_submit(self) -> None:
        self.post_message(ChangeAttributesDialog.Submitted(self, self._compute_mode()))

    def action_cancel(self) -> None:
        self.post_message(ChangeAttributesDialog.Cancelled(self))


class FindFileDialog(FocusChainMixin, Container, WindowContent):
    """Far Manager-style "Find file" dialog.

    Two text inputs (mask, contains-text) plus five checkboxes plus
    Find / Cancel buttons. Posts :class:`Submitted` with a fully-built
    :class:`FindOptions` value when Find is pressed (or Enter while a
    text input is focused). Pressing Find with an empty mask is a no-op
    so the user cannot accidentally start a "match-everything" search;
    they must type at least ``*`` if they really want it.
    """

    can_focus = False

    BINDINGS = [
        Binding("escape", "cancel", show=False),
    ]

    DEFAULT_CSS = """
    FindFileDialog {
        layout: vertical;
        background: $surface;
    }
    FindFileDialog Static.ff-label {
        margin: 1 1 0 1;
        color: $text;
    }
    FindFileDialog Input {
        margin: 0 1;
        height: 1;
        padding: 0 1;
        border: none;
        background: $boost;
        color: $text;
    }
    FindFileDialog Input:focus {
        background: $accent;
        color: $text;
        border: none;
    }
    FindFileDialog #ff-checks {
        layout: horizontal;
        height: auto;
        margin: 1 1 0 1;
    }
    FindFileDialog #ff-checks-left, FindFileDialog #ff-checks-right {
        width: 1fr;
        height: auto;
        layout: vertical;
    }
    FindFileDialog #ff-buttons {
        height: 2;
        align: center middle;
        margin-top: 1;
    }
    """

    class Submitted(Message):
        def __init__(self, dialog: "FindFileDialog", options: "FindOptions") -> None:
            self.dialog = dialog
            self.options = options
            super().__init__()

    class Cancelled(Message):
        def __init__(self, dialog: "FindFileDialog") -> None:
            self.dialog = dialog
            super().__init__()

    def __init__(
        self,
        *,
        start_dir,
        initial_mask: str = "*",
        initial_contains: str = "",
        case_sensitive_mask: bool = False,
        case_sensitive_text: bool = False,
        whole_words: bool = False,
        search_for_folders: bool = True,
        follow_symlinks: bool = True,
        context: object | None = None,
    ) -> None:
        super().__init__()
        self.start_dir = start_dir
        self.context = context
        self.window_title = "Find file"
        self._initial_mask = initial_mask
        self._initial_contains = initial_contains
        self._initial_flags = {
            "ff-cs-mask":   case_sensitive_mask,
            "ff-cs-text":   case_sensitive_text,
            "ff-whole":     whole_words,
            "ff-folders":   search_for_folders,
            "ff-symlinks":  follow_symlinks,
        }
        self._mask_input = Input(id="ff-mask")
        self._text_input = Input(id="ff-text")
        self._checkboxes: dict[str, _PermCheckbox] = {}

    def compose(self) -> ComposeResult:
        yield Static("A file [u]m[/u]ask or several file masks:", classes="ff-label", markup=True)
        yield self._mask_input
        yield Static("Cont[u]a[/u]ining text:", classes="ff-label", markup=True)
        yield self._text_input
        with Container(id="ff-checks"):
            with Container(id="ff-checks-left"):
                yield _PermCheckbox(
                    label_markup="Case [u]s[/u]ensitive file masks",
                    mask=0,
                    checked=self._initial_flags["ff-cs-mask"],
                )
                yield _PermCheckbox(
                    label_markup="[u]C[/u]ase sensitive text",
                    mask=0,
                    checked=self._initial_flags["ff-cs-text"],
                )
                yield _PermCheckbox(
                    label_markup="[u]W[/u]hole words",
                    mask=0,
                    checked=self._initial_flags["ff-whole"],
                )
            with Container(id="ff-checks-right"):
                yield _PermCheckbox(
                    label_markup="Search for f[u]o[/u]lders",
                    mask=0,
                    checked=self._initial_flags["ff-folders"],
                )
                yield _PermCheckbox(
                    label_markup="Search in symbolic lin[u]k[/u]s",
                    mask=0,
                    checked=self._initial_flags["ff-symlinks"],
                )
        with Horizontal(id="ff-buttons"):
            yield ShadowButton(
                "Find",
                id="ff-find",
                face_bg="rgb(0,160,90)",
                hotkey="f",
            )
            yield ShadowButton(
                "Cancel",
                id="ff-cancel",
                face_bg="rgb(160,40,40)",
                hotkey="c",
            )

    def on_mount(self) -> None:
        self._mask_input.value = self._initial_mask
        self._text_input.value = self._initial_contains
        # Snapshot the per-id checkboxes so _build_options / focus chain
        # see them in deterministic order.
        boxes = list(self.query(_PermCheckbox))
        # The compose order is: cs-mask, cs-text, whole, folders, symlinks.
        keys = ["ff-cs-mask", "ff-cs-text", "ff-whole", "ff-folders", "ff-symlinks"]
        for key, box in zip(keys, boxes):
            self._checkboxes[key] = box
        self.call_after_refresh(self.focus_input)

    # --- public API used by app shell + tests --------------------------

    def focus_input(self) -> None:
        self._mask_input.focus()

    def get_mask_text(self) -> str:
        return self._mask_input.value

    def get_contains_text(self) -> str:
        return self._text_input.value

    def is_checked(self, key: str) -> bool:
        box = self._checkboxes.get(key)
        return bool(box and box.checked)

    def _focusables(self) -> list[Widget]:
        chain: list[Widget] = [self._mask_input, self._text_input]
        for key in ("ff-cs-mask", "ff-cs-text", "ff-whole", "ff-folders", "ff-symlinks"):
            box = self._checkboxes.get(key)
            if box is not None:
                chain.append(box)
        try:
            chain.append(self.query_one("#ff-find", ShadowButton))
            chain.append(self.query_one("#ff-cancel", ShadowButton))
        except Exception:
            pass
        return chain

    def _build_options(self) -> "FindOptions | None":
        from dunders.fm.find_file import FindOptions, parse_masks

        masks = parse_masks(self._mask_input.value)
        if not masks:
            return None
        return FindOptions(
            masks=masks,
            case_sensitive_mask=self.is_checked("ff-cs-mask"),
            contains=self._text_input.value,
            case_sensitive_text=self.is_checked("ff-cs-text"),
            whole_words=self.is_checked("ff-whole"),
            search_for_folders=self.is_checked("ff-folders"),
            follow_symlinks=self.is_checked("ff-symlinks"),
        )

    def action_submit(self) -> None:
        opts = self._build_options()
        if opts is None:
            # Empty mask — nudge focus back to mask input, do nothing.
            self._mask_input.focus()
            return
        self.post_message(FindFileDialog.Submitted(self, opts))

    def action_cancel(self) -> None:
        self.post_message(FindFileDialog.Cancelled(self))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.action_submit()

    def on_shadow_button_pressed(self, event: "ShadowButton.Pressed") -> None:
        event.stop()
        if event.button.id == "ff-find":
            self.action_submit()
        elif event.button.id == "ff-cancel":
            self.action_cancel()
