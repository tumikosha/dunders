"""CommandLine — multi-line, soft-wrapping shell input docked above StatusBar.

The widget is built on top of Textual's :class:`TextArea` so long inputs wrap
to the next visual row and the cmdline grows in height to fit, capped at
``_MAX_VISIBLE_LINES``. Key bindings:

* Enter — submit the current buffer; if the buffer ends with a backslash
  ``\\`` (bash-style line continuation) the trailing slash is consumed and a
  newline is inserted instead. This is the universal multi-line entry path
  that works in every terminal.
* Shift+Enter / Alt+Enter / Ctrl+Enter / Cmd+Enter — insert a newline
  directly. These only work in terminals that send a distinct escape for
  the modifier (Ghostty, iTerm2, WezTerm, Kitty, Alacritty, …). In stock
  macOS Terminal use the ``\\``-Enter path instead.
* Up/Down — navigate persistent history when the cursor is on the first /
  last logical row; otherwise move the cursor between rows.
* Ctrl+C — send SIGINT to the running command (or clear the buffer if it's
  non-empty); same as a shell.
* Ctrl+D — when the buffer is empty, send EOF (close the child's stdin).
* Ctrl+\\ — force-kill the running child (SIGKILL).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Callable

from textual._ansi_sequences import ANSI_SEQUENCES_KEYS
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.message import Message
from textual.widgets import Static, TextArea

from dunders.fm.console.history import History
from dunders.windowing.core import clipboard


# --- Patch missing ANSI sequence mappings -------------------------------
# macOS Terminal.app and many other terminals send ESC+CR for Alt+Enter
# (and Shift+Enter when configured to use Option as Meta). Textual's
# default xterm parser strips the Alt prefix from multi-char key names
# like "enter", so the event arrives as plain "enter". Register the raw
# sequence so it is parsed as the modified key directly.
for _seq, _key in (
    ("\x1b\r", "alt+enter"),
    ("\x1b\n", "alt+enter"),
):
    ANSI_SEQUENCES_KEYS.setdefault(_seq, (SimpleNamespace(value=_key),))
del _seq, _key


_MAX_VISIBLE_LINES = 10


class _CmdInput(TextArea):
    """TextArea variant whose Enter submits and whose Alt+Enter is newline."""

    BINDINGS = [
        Binding("enter", "cmd_submit", "Submit", show=False, priority=True),
        Binding("shift+enter", "cmd_newline", "Newline", show=False, priority=True),
        Binding("alt+enter", "cmd_newline", "Newline", show=False, priority=True),
        Binding("ctrl+enter", "cmd_newline", "Newline", show=False, priority=True),
        Binding("super+enter", "cmd_newline", "Newline", show=False, priority=True),
        Binding("ctrl+j", "cmd_newline", "Newline", show=False, priority=True),
        Binding("ctrl+c", "cmd_ctrl_c", "Cancel/Clear", show=False, priority=True),
        Binding("ctrl+d", "cmd_ctrl_d", "EOF", show=False, priority=True),
        Binding("ctrl+backslash", "cmd_kill", "Kill", show=False, priority=True),
        Binding("up", "cmd_up", "Prev/Up", show=False, priority=True),
        Binding("down", "cmd_down", "Next/Down", show=False, priority=True),
    ]

    def __init__(self, owner: "CommandLine", *, id: str | None = None) -> None:
        super().__init__(
            "",
            soft_wrap=True,
            show_line_numbers=False,
            tab_behavior="focus",
            id=id,
        )
        self._owner = owner

    def on_resize(self, event) -> None:
        # When the cmdline grows in height (newline inserted, wrap, ...) the
        # bottom-stack dock bar pushes Desktop up. Desktop has explicit-sized
        # children (panel and console windows) laid out by the app's tiling
        # routine — without a re-tile they keep their old heights and clip /
        # overflow the new Desktop area. Ask the app to re-apply layout.
        app = self.app
        relayout = getattr(app, "_apply_default_layout", None)
        if relayout is not None:
            app.call_after_refresh(relayout)

    # --- Input-API compatibility shims -----------------------------------
    # The rest of the app (and tests) historically poked at ``inp.value`` /
    # ``inp.cursor_position``. Provide thin shims so we don't have to touch
    # every call site when switching from Input to TextArea.
    @property
    def value(self) -> str:
        return self.text

    @value.setter
    def value(self, new: str) -> None:
        self.load_text(new)
        self._move_cursor_to_end()

    @property
    def cursor_position(self) -> int:
        row, col = self.cursor_location
        offset = 0
        for line in self.document.lines[:row]:
            offset += len(line) + 1  # +1 for the newline
        return offset + col

    @cursor_position.setter
    def cursor_position(self, _value: int) -> None:
        # Best-effort: snap to the end. Callers only ever use this to put
        # the cursor after a freshly-set value.
        self._move_cursor_to_end()

    def _move_cursor_to_end(self) -> None:
        last_row = max(0, self.document.line_count - 1)
        last_col = len(self.document.lines[last_row]) if self.document.line_count else 0
        self.move_cursor((last_row, last_col))

    # --- Actions ---------------------------------------------------------
    def action_cmd_submit(self) -> None:
        # Bash-style line continuation: a trailing backslash on the line
        # the cursor is on turns Enter into a newline. This is the only
        # multi-line entry path that's terminal-agnostic — Shift/Alt+Enter
        # require the terminal to emit distinct escape sequences, which not
        # all terminals do.
        row, col = self.cursor_location
        if 0 <= row < self.document.line_count:
            line = self.document.lines[row]
            if col > 0 and col == len(line) and line.endswith("\\"):
                self.move_cursor((row, col - 1), select=True)
                self.action_delete_left()
                self.insert("\n")
                return
        self._owner._submit(anonymous=False)

    def action_cmd_newline(self) -> None:
        self.insert("\n")

    def action_cmd_ctrl_c(self) -> None:
        self._owner.action_ctrl_c()

    def action_cmd_ctrl_d(self) -> None:
        self._owner.action_ctrl_d()

    def action_cmd_kill(self) -> None:
        self._owner.post_message(CommandLine.KillRequested())

    # --- Clipboard: route TextArea copy/cut/paste through the system buffer --
    # By default Textual's TextArea uses app.copy_to_clipboard / app.clipboard
    # (its own internal buffer + OSC 52), which on Terminal.app does NOT reach
    # the real system clipboard the editor uses. Override so the command line
    # shares ONE system clipboard with the editor. (Cmd+V via bracketed paste
    # is handled by the terminal itself and already inserts the system buffer.)
    def action_copy(self) -> None:
        if self.selected_text:
            clipboard.copy(self.selected_text, app=self.app)

    def action_cut(self) -> None:
        selected = self.selected_text
        if selected:
            clipboard.copy(selected, app=self.app)
            self.delete(*self.selection, maintain_selection_offset=False)

    def action_paste(self) -> None:
        text = clipboard.paste(app=self.app)
        if text:
            self.insert(text)

    def action_cmd_up(self) -> None:
        row, _col = self.cursor_location
        if row == 0:
            # At the top of the buffer: if file panels are visible the app
            # consumes up/down to drive the active panel's cursor; otherwise
            # (console-only / editor / cli modes) navigate command history.
            if not self._owner._route_nav(-1):
                self._owner.history_prev()
        else:
            self.action_cursor_up()

    def action_cmd_down(self) -> None:
        row, _col = self.cursor_location
        last = max(0, self.document.line_count - 1)
        if row >= last:
            if not self._owner._route_nav(1):
                self._owner.history_next()
        else:
            self.action_cursor_down()


class _RunModeChip(Static):
    """Clickable chip showing the current run mode; click toggles it.

    Lives next to the command line. The app owns the actual mode state and the
    label text — the chip just reports a click so the app can flip it.
    """

    def __init__(self, owner: "CommandLine") -> None:
        super().__init__("", classes="cmdline-mode")
        self._owner = owner

    def on_click(self, event) -> None:
        event.stop()
        self._owner.post_message(CommandLine.RunModeToggleRequested())


class CommandLine(Container):
    """Multi-line shell input with soft wrap, dynamic height, and history."""

    DEFAULT_CSS = f"""
    CommandLine {{
        height: auto;
    }}
    CommandLine Horizontal {{
        height: auto;
    }}
    CommandLine Static.cmdline-hint {{
        width: auto;
        height: 1;
        padding: 0 1;
        color: $text-muted;
        background: $surface;
    }}
    CommandLine Static.cmdline-mode {{
        width: auto;
        height: 1;
        padding: 0 1;
        color: $text;
        background: $panel;
        text-style: bold;
    }}
    CommandLine Static.cmdline-mode:hover {{
        background: $accent;
    }}
    CommandLine TextArea {{
        border: none;
        padding: 0 1;
        height: auto;
        max-height: {_MAX_VISIBLE_LINES};
        background: $surface;
    }}
    CommandLine TextArea:focus {{
        border: none;
    }}
    """

    BINDINGS = [
        Binding("alt+enter", "submit_anonymous", "Submit→new window", show=False),
    ]

    class Submitted(Message):
        def __init__(self, text: str, anonymous: bool = False) -> None:
            self.text = text
            self.anonymous = anonymous
            super().__init__()

    class CancelRequested(Message):
        """Posted on Ctrl+C with empty input — app cancels current command."""

    class EofRequested(Message):
        """Posted on Ctrl+D with empty input — app closes child stdin."""

    class KillRequested(Message):
        """Posted on Ctrl+\\ — app force-kills the running child."""

    class RunModeToggleRequested(Message):
        """Posted when the user clicks the run-mode chip — app flips the
        terminal handover mode (relay ↔ suspend)."""

    def __init__(self, id: str | None = None, *, history: History | None = None) -> None:
        super().__init__(id=id)
        self._hint = Static("[Alt+C]", classes="cmdline-hint")
        self._input = _CmdInput(self, id="cmdline-input")
        self._mode_chip = _RunModeChip(self)
        self._mode_chip.display = False  # shown once the app sets a label
        self._history = history
        self._subscribers: list[Callable[[CommandLine.Submitted], None]] = []
        # Optional app-supplied hook: given a direction (-1 up / +1 down) it
        # routes up/down to the active file panel and returns True when it
        # consumed the key. When it returns False (or is unset) the cmdline
        # falls back to command-history navigation. See app._cmdline_panel_nav.
        self._panel_nav: Callable[[int], bool] | None = None

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield self._hint
            yield self._input
            yield self._mode_chip

    def set_run_mode(self, label: str, *, tooltip: str | None = None) -> None:
        """Set the run-mode chip text (and tooltip). Called by the app to
        reflect the current terminal handover mode. Passing an empty label
        hides the chip."""
        if not label:
            self._mode_chip.display = False
            return
        self._mode_chip.update(label)
        self._mode_chip.tooltip = tooltip
        self._mode_chip.display = True

    @property
    def text(self) -> str:
        return self._input.text

    def set_text(self, value: str) -> None:
        self._input.value = value

    def insert_at_cursor(self, text: str) -> None:
        """Insert text at the command-line cursor; the cursor ends after it."""
        self._input.insert(text)

    def subscribe(self, fn: Callable[["CommandLine.Submitted"], None]) -> None:
        """Used by tests to receive submissions without going through Textual messaging."""
        self._subscribers.append(fn)

    def set_panel_nav(self, fn: Callable[[int], bool] | None) -> None:
        """Install the app hook that diverts boundary up/down to a file panel."""
        self._panel_nav = fn

    def _route_nav(self, direction: int) -> bool:
        """Return True if the app consumed this up/down (panel-cursor move)."""
        if self._panel_nav is None:
            return False
        return bool(self._panel_nav(direction))

    def history_prev(self) -> None:
        if self._history is None:
            return
        text = self._history.previous()
        if text:
            self._input.value = text

    def history_next(self) -> None:
        if self._history is None:
            return
        self._input.value = self._history.next()

    def action_history_prev(self) -> None:
        self.history_prev()

    def action_history_next(self) -> None:
        self.history_next()

    def action_submit_anonymous(self) -> None:
        # Kept for backward-compatibility callers/tests; the user-facing
        # alt+enter binding now inserts a newline instead.
        self._submit(anonymous=True)

    def action_ctrl_c(self) -> None:
        # Smart Ctrl+C: copy the selection to the system clipboard when there
        # is one (works in every terminal, mirrors the editor's Ctrl+C). With
        # no selection, keep the shell-like behaviour: clear buffer, else
        # interrupt the running command.
        selected = self._input.selected_text
        if selected:
            clipboard.copy(selected, app=self.app)
            return
        if self._input.text:
            self._input.value = ""
            return
        self.post_message(CommandLine.CancelRequested())

    def action_ctrl_d(self) -> None:
        # Empty buffer -> EOF for child. Non-empty buffer -> let the
        # default TextArea ctrl+d (delete forward) run by doing nothing
        # here; the binding dispatch will fall through to the parent class.
        if self._input.text:
            return
        self.post_message(CommandLine.EofRequested())

    def set_busy(self, busy: bool, *, label: str | None = None) -> None:
        """Update the left-side hint to reflect whether the active console
        target is currently running a command. Called from the app whenever
        target.busy flips."""
        if busy:
            text = f"[{label}: stdin]" if label else "[stdin]"
        else:
            text = "[Alt+C]"
        self._hint.update(text)

    def _submit(self, *, anonymous: bool) -> None:
        text = self._input.text
        self._input.value = ""
        if self._history is not None:
            self._history.append(text)
            self._history.reset_cursor()
        msg = CommandLine.Submitted(text, anonymous=anonymous)
        for fn in self._subscribers:
            fn(msg)
        self.post_message(msg)

    def submit(self) -> None:
        """Public alias for backward compatibility — submits as non-anonymous."""
        self._submit(anonymous=False)
