from pathlib import Path

from textual.app import App, ComposeResult

from dunders.fm.user_menu import MenuEntry
from dunders.fm.user_menu_loader import Row
from dunders.fm.user_menu_dialog import UserMenuDialog


def _rows():
    local = MenuEntry(hotkey="l", title="Local cmd", body="echo local", section="Local")
    glob = MenuEntry(hotkey="g", title="Global cmd", body="echo global", section="Global")
    return [
        Row(kind="header", text="Local"),
        Row(kind="entry", entry=local, source=Path("/local/.dunders.menu.md")),
        Row(kind="separator"),
        Row(kind="header", text="Global"),
        Row(kind="entry", entry=glob, source=Path("/global/menu.md")),
    ]


class _Host(App):
    def __init__(self, rows):
        super().__init__()
        self._rows = rows
        self.events = []

    def compose(self) -> ComposeResult:
        yield UserMenuDialog(self._rows, default_source=Path("/global/menu.md"))

    def on_user_menu_dialog_selected(self, event):
        self.events.append(("selected", event.entry.title))

    def on_user_menu_dialog_edit_requested(self, event):
        self.events.append(("edit", str(event.source)))

    def on_user_menu_dialog_cancelled(self, event):
        self.events.append(("cancelled", None))


async def test_hotkey_selects_matching_entry():
    app = _Host(_rows())
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("g")
        await pilot.pause()
    assert ("selected", "Global cmd") in app.events


async def test_f4_emits_edit_for_highlighted_source():
    app = _Host(_rows())
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("f4")
        await pilot.pause()
    assert any(kind == "edit" for kind, _ in app.events)


async def test_escape_cancels():
    app = _Host(_rows())
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert ("cancelled", None) in app.events


async def test_dialog_shows_f4_edit_footer():
    from textual.widgets import Static

    app = _Host(_rows())
    async with app.run_test() as pilot:
        await pilot.pause()
        footer = app.query_one("#um-footer", Static)
        assert "F4-Edit" in str(footer.render())
