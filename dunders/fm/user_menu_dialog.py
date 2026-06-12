"""Modal selection list for the User Menu (F2)."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Container
from textual.message import Message
from textual.widgets import ListItem, ListView, Static

from dunders.fm.user_menu import MenuEntry
from dunders.fm.user_menu_loader import Row
from dunders.windowing import WindowContent


class UserMenuDialog(Container, WindowContent):
    """Flat list with non-selectable section headers + a local/global
    separator. Enter/click or an entry's hotkey runs it; F4 edits the
    source file; Esc cancels."""

    can_focus = False

    DEFAULT_CSS = """
    UserMenuDialog { layout: vertical; }
    UserMenuDialog ListView { height: 1fr; background: $panel; }
    UserMenuDialog ListItem { padding: 0 1; }
    UserMenuDialog #um-footer {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $text;
        text-style: dim;
    }
    """

    class Selected(Message):
        def __init__(self, dialog: "UserMenuDialog", entry: MenuEntry) -> None:
            self.dialog = dialog
            self.entry = entry
            super().__init__()

    class EditRequested(Message):
        def __init__(self, dialog: "UserMenuDialog", source: Path) -> None:
            self.dialog = dialog
            self.source = source
            super().__init__()

    class Cancelled(Message):
        def __init__(self, dialog: "UserMenuDialog") -> None:
            self.dialog = dialog
            super().__init__()

    def __init__(self, rows: list[Row], *, default_source: Path) -> None:
        super().__init__()
        self._rows = rows
        self._default_source = default_source
        self.window_title = "User menu"
        self._list = ListView()
        self._footer = Static("F4-Edit", id="um-footer")

    def compose(self) -> ComposeResult:
        yield self._list
        yield self._footer

    def on_mount(self) -> None:
        first_entry_index: int | None = None
        for idx, row in enumerate(self._rows):
            if row.kind == "header":
                item = ListItem(Static(f"[b]{row.text}[/b]"))
                item.disabled = True
            elif row.kind == "separator":
                item = ListItem(Static("─" * 24))
                item.disabled = True
            else:
                e = row.entry
                label = f"({e.hotkey}) {e.title}" if e.hotkey else f"    {e.title}"
                item = ListItem(Static(label))
                item._menu_entry = e            # type: ignore[attr-defined]
                item._menu_source = row.source  # type: ignore[attr-defined]
                if first_entry_index is None:
                    first_entry_index = idx
            self._list.append(item)
        self._list.focus()
        if first_entry_index is not None:
            self._list.index = first_entry_index

    # --- helpers (also used by tests) ------------------------------------

    def entry_for_hotkey(self, ch: str) -> MenuEntry | None:
        ch = ch.lower()
        for row in self._rows:
            if row.kind == "entry" and row.entry.hotkey == ch:
                return row.entry  # local rows come first -> local wins
        return None

    def _highlighted_source(self) -> Path | None:
        child = self._list.highlighted_child
        return getattr(child, "_menu_source", None) if child is not None else None

    # --- input -----------------------------------------------------------

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        entry = getattr(event.item, "_menu_entry", None)
        if entry is not None:
            event.stop()
            self.post_message(self.Selected(self, entry))

    def on_key(self, event) -> None:
        key = event.key
        if key == "escape":
            event.stop()
            self.post_message(self.Cancelled(self))
            return
        if key == "f4":
            event.stop()
            self.post_message(
                self.EditRequested(self, self._highlighted_source() or self._default_source)
            )
            return
        if len(key) == 1 and key.isprintable():
            entry = self.entry_for_hotkey(key)
            if entry is not None:
                event.stop()
                self.post_message(self.Selected(self, entry))
