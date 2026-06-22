"""CommandHistoryDialog — mc-style popup of the command-line history.

Opened with Alt+H (app-level command ``cmd.history``). Lists past commands
newest-first; Enter / click recalls the selected one into the command line;
Esc / Close dismisses. Callback-driven and palette-themed, like the other
dunder dialogs (mirrors ``SqlHistoryDialog``).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from textual.containers import Container, Horizontal
from textual.widgets import DataTable

from dunders.fm.dialogs import ShadowButton
from dunders.windowing.content import WindowContent
from dunders.windowing.helpers import ModalWindow
from dunders.windowing.palette import Palette
from dunders.windowing.window import Window


__all__ = ["CommandHistoryDialog"]


class CommandHistoryDialog(Container, WindowContent):
    can_focus = False

    DEFAULT_CSS = """
    CommandHistoryDialog { layout: vertical; width: 80; height: auto;
                           max-height: 22; padding: 1 1; }
    CommandHistoryDialog DataTable { height: auto; max-height: 18; }
    CommandHistoryDialog #ch-empty { margin: 1; color: $text-muted; }
    CommandHistoryDialog #ch-buttons { height: 1; align: center middle;
                                       margin-top: 1; }
    """

    def __init__(self, entries: Sequence[str], *, on_pick: Callable[[str], None]) -> None:
        super().__init__()
        self.window_title = "Command history"
        self._entries = list(entries)  # newest-first
        self._on_pick = on_pick
        self._table = DataTable(id="ch-table", cursor_type="row", zebra_stripes=False)

    def compose(self):
        yield self._table
        with Horizontal(id="ch-buttons"):
            yield ShadowButton("Close", id="ch-close", face_bg="rgb(80,80,90)")

    def on_mount(self) -> None:
        self._table.add_column("Command")
        for cmd in self._entries:
            self._table.add_row(cmd)
        self.apply_theme()
        self.call_after_refresh(self.focus_list)

    def focus_list(self) -> None:
        try:
            self._table.focus()
        except Exception:
            pass

    def _get_palette(self) -> Palette | None:
        try:
            for anc in self.ancestors_with_self:
                pal = getattr(anc, "palette", None)
                if isinstance(pal, Palette):
                    return pal
        except Exception:
            return None
        return None

    def apply_theme(self) -> None:
        palette = self._get_palette()
        if palette is not None:
            content = palette.get("window.content")
            for node in (self, self._table):
                if content.bg is not None:
                    node.styles.background = content.bg
                if content.fg is not None:
                    node.styles.color = content.fg
        self.refresh()

    def on_data_table_row_selected(self, event) -> None:
        row = getattr(event, "cursor_row", None)
        if row is None or not (0 <= row < len(self._entries)):
            return
        self._on_pick(self._entries[row])
        self._dismiss()

    def on_shadow_button_pressed(self, event: ShadowButton.Pressed) -> None:
        event.stop()
        if (event.button.id or "") == "ch-close":
            self._dismiss()

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self._dismiss()

    def _dismiss(self) -> None:
        node = self
        while node is not None:
            if isinstance(node, ModalWindow):
                node.post_message(Window.Closed(node))
                return
            node = getattr(node, "parent", None)
