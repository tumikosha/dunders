"""Filterable language picker (modal) for choosing a syntax-highlight lexer.

Modeled on the windowing CommandPaletteContent: a searchable list of all
Pygments languages. Picking one posts ``Picked`` with the chosen lexer alias;
``Esc`` dismisses. Open via :func:`show_language_picker`.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from rich.segment import Segment
from textual import events
from textual.message import Message
from textual.reactive import reactive
from textual.strip import Strip

from pygments.lexers import get_all_lexers

from dunders.windowing.content import WindowContent
from dunders.windowing.frame import BorderStyle, Decorations, TitleSpec
from dunders.windowing.helpers import ModalWindow

if TYPE_CHECKING:
    from dunders.windowing.desktop import Desktop
    from dunders.windowing.editor.content import EditorContent


__all__ = ["LanguagePickerContent", "show_language_picker", "language_entries"]


@lru_cache(maxsize=1)
def language_entries() -> list[tuple[str, str]]:
    """All Pygments languages as (display_name, primary_alias), sorted by name.

    Cached — the lexer registry is static for the process lifetime. Languages
    without an alias are skipped; duplicate primary aliases are de-duped.
    """
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for name, aliases, _filenames, _mimetypes in get_all_lexers():
        if not aliases:
            continue
        alias = aliases[0]
        if alias in seen:
            continue
        seen.add(alias)
        out.append((name, alias))
    out.sort(key=lambda e: e[0].lower())
    return out


class LanguagePickerContent(WindowContent):
    """Searchable list of Pygments languages. Lives inside a ModalWindow."""

    can_focus = True

    DEFAULT_CSS = """
    LanguagePickerContent {
        layer: overlay;
    }
    """

    query: reactive[str] = reactive("")
    highlight: reactive[int] = reactive(0)

    class Picked(Message):
        def __init__(self, picker: "LanguagePickerContent", language: str) -> None:
            self.picker = picker
            self.language = language
            super().__init__()

    class Dismissed(Message):
        def __init__(self, picker: "LanguagePickerContent") -> None:
            self.picker = picker
            super().__init__()

    def __init__(self, editor: "EditorContent") -> None:
        super().__init__()
        self.editor = editor
        self._all = language_entries()

    @property
    def filtered(self) -> list[tuple[str, str]]:
        q = self.query.lower().strip()
        if not q:
            return list(self._all)
        return [e for e in self._all if q in e[0].lower() or q in e[1].lower()]

    def watch_query(self, _old: str, _new: str) -> None:
        self.highlight = 0
        if self.is_mounted:
            self.refresh()

    def watch_highlight(self, _old: int, _new: int) -> None:
        if self.is_mounted:
            self.refresh()

    def on_mount(self) -> None:
        self.focus()

    def on_key(self, event: events.Key) -> None:
        k = event.key
        if k == "escape":
            self.post_message(LanguagePickerContent.Dismissed(self))
            event.stop()
            return
        if k == "enter":
            items = self.filtered
            if 0 <= self.highlight < len(items):
                self.post_message(LanguagePickerContent.Picked(self, items[self.highlight][1]))
            event.stop()
            return
        if k == "up":
            items = self.filtered
            if items:
                self.highlight = (self.highlight - 1) % len(items)
            event.stop()
            return
        if k == "down":
            items = self.filtered
            if items:
                self.highlight = (self.highlight + 1) % len(items)
            event.stop()
            return
        if k == "backspace":
            self.query = self.query[:-1]
            event.stop()
            return
        if event.character and event.character.isprintable() and len(event.character) == 1:
            self.query = self.query + event.character
            event.stop()
            return

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        height = self.size.height
        if width <= 0 or height <= 0:
            return Strip.blank(0)
        try:
            base = self.parent.palette  # type: ignore[attr-defined]
        except Exception:
            base = None
        item_style = base.rich_style("menu.item") if base is not None else None
        active_style = base.rich_style("menu.item.active") if base is not None else item_style
        # Language entries are always enabled and have no hotkey column, so
        # (unlike the command palette) only item/active styles are needed.

        if y == 0:
            text = f"> {self.query}".ljust(width)[:width]
            return Strip([Segment(text, active_style)])
        if y == 1:
            return Strip([Segment("─" * width, item_style)])

        items = self.filtered
        idx = y - 2
        if idx >= len(items):
            return Strip([Segment(" " * width, item_style)])
        name, alias = items[idx]
        active = idx == self.highlight
        row_style = active_style if active else item_style
        left = f" {name}"
        hint = f"{alias} "
        pad = max(1, width - len(left) - len(hint))
        line = left + " " * pad + hint
        if len(line) > width:
            line = line[: width - 1] + "…"
        elif len(line) < width:
            line += " " * (width - len(line))
        return Strip([Segment(line, row_style)])


def show_language_picker(
    desktop: "Desktop",
    editor: "EditorContent",
    *,
    size: tuple[int, int] = (50, 18),
) -> ModalWindow:
    """Open the language picker as a modal centred on ``desktop``."""
    W, H = desktop.size
    sw = min(size[0], max(10, W - 2))
    sh = min(size[1], max(5, H - 2))
    x = max(0, (W - sw) // 2)
    y = max(0, (H - sh) // 2)
    content = LanguagePickerContent(editor)
    modal = ModalWindow(
        content,
        title=TitleSpec(text="Select Language", align="center"),
        position=(x, y),
        size=(sw, sh),
        border_focused=BorderStyle.DOUBLE,
        border_unfocused=BorderStyle.DOUBLE,
        decorations=Decorations(close_box=True),
    )
    desktop.add_window(modal)
    desktop._modal_stack = getattr(desktop, "_modal_stack", [])
    desktop._modal_stack.append(modal)
    return modal
