"""ViewerContent — read-only EditorContent for F3 (View).

Shares all navigation, search, and fold UX with the editor; disables
every path that would mutate the buffer by replacing the buffer's
mutation methods with no-ops at mount time. The editor widget still
intercepts the keystrokes (so navigation/search/folding all work)
but its calls to insert_char / delete_* / etc. become silent no-ops.

Why intercept at the buffer layer instead of at on_key on the widget?
Textual's message dispatcher caches handlers via class lookup, so
setting instance attributes on EditorWidget.on_key doesn't reliably
take effect. The buffer is the actual mutation point; blocking writes
there is both simpler and more thorough.
"""

from __future__ import annotations

from tyui.windowing.editor.content import EditorContent


__all__ = ["ViewerContent"]


def _noop(*_args, **_kwargs) -> None:
    return None


class ViewerContent(EditorContent):
    """Read-only file viewer."""

    _DISABLED_BUFFER_METHODS = (
        "insert_char",
        "insert_newline",
        "delete_char_backward",
        "delete_char_forward",
        "delete_word_backward",
        "delete_line",
        "duplicate_line",
        "paste",
        "delete_selection",
    )

    def on_mount(self) -> None:
        for name in self._DISABLED_BUFFER_METHODS:
            if hasattr(self._buffer, name):
                setattr(self._buffer, name, _noop)
        # Focus the inner editor widget directly so arrow keys and search
        # work immediately on open. The Window framework focuses the
        # WindowContent wrapper, but EditorContent is non-focusable; the
        # input widget is _editor.
        self._editor.focus()
