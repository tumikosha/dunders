"""Key Probe — a diagnostic window that echoes every keypress Textual decodes.

Some keys (notably Ctrl+digit and Alt+letter) never reach the app on macOS
Terminal.app / iTerm2 because the terminal emits no escape sequence for them.
The Key Probe is the in-app way to check what *does* arrive: it captures every
``Key`` event, shows the most recent one large, and keeps a scrolling log of
the raw decode (``key`` / ``character`` / ``aliases``).

The window is closed the normal way (its close box, or Esc / the Windows menu);
``on_key`` deliberately does NOT call ``event.stop()`` for Esc/F10 so the user
can still bail out via the app-level bindings.
"""

from __future__ import annotations

from textual import events
from textual.containers import Vertical
from textual.widgets import RichLog, Static

from dunders.windowing.content import WindowContent


class KeyProbeContent(WindowContent):
    """Focusable diagnostic content that logs every key it receives.

    ``can_focus = True`` so the window funnels keystrokes straight here once
    focused — that is what lets the probe observe keys that would otherwise be
    routed elsewhere through the focus-scoped command system.
    """

    can_focus = True

    DEFAULT_CSS = """
    KeyProbeContent {
        layout: vertical;
    }
    KeyProbeContent > #key-probe-hint {
        height: 1;
        color: $text-muted;
    }
    KeyProbeContent > #key-probe-last {
        height: 3;
        content-align: center middle;
        text-style: bold;
    }
    KeyProbeContent > #key-probe-log {
        height: 1fr;
    }
    """

    _HINT = "Press keys — see what reaches the app.  Esc / close box to exit."

    def __init__(self) -> None:
        super().__init__()
        self.window_title = "Key Probe"
        self.window_subtitle = "Press keys to see what reaches the app"
        self._last: Static | None = None
        self._log: RichLog | None = None

    def compose(self):
        with Vertical():
            yield Static(self._HINT, id="key-probe-hint")
            yield Static("(waiting for a keypress…)", id="key-probe-last")
            yield RichLog(id="key-probe-log", highlight=False, markup=False, wrap=False)

    def on_mount(self) -> None:
        self._last = self.query_one("#key-probe-last", Static)
        self._log = self.query_one("#key-probe-log", RichLog)
        self.focus()

    def on_window_focus(self) -> None:
        # Re-grab focus when the window is activated so keys land on the probe.
        self.focus()

    def on_key(self, event: events.Key) -> None:
        """Log every key. Diagnostics need to see ALL keys, so we do not call
        ``event.stop()`` here — the keystroke is reported and still bubbles, so
        the app-level Esc / F10 bindings keep working as an escape hatch.
        """
        line = (
            f"key={event.key}   "
            f"char={event.character!r}   "
            f"aliases={list(event.aliases)}"
        )
        if self._log is not None:
            self._log.write(line)
        if self._last is not None:
            self._last.update(f"key = {event.key}")
