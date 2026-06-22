"""Command-history popup (Alt+H): listing, recall, empty case, key binding."""

import pytest

from dunders.app import DundersApp
from dunders.fm.cmd_history_dialog import CommandHistoryDialog
from dunders.fm.commandline import CommandLine


@pytest.mark.asyncio
async def test_history_popup_lists_newest_first_and_recalls(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()
        app.command_history.append("ls -la")
        app.command_history.append("git status")
        app.action_show_command_history()
        await pilot.pause()
        dialog = app.query_one(CommandHistoryDialog)
        assert dialog._entries[0] == "git status"   # newest first
        assert dialog._entries[1] == "ls -la"
        # recall the newest into the command line
        dialog._on_pick("git status")
        dialog._dismiss()
        await pilot.pause()
        assert app.command_line.text == "git status"


@pytest.mark.asyncio
async def test_history_popup_empty_shows_no_dialog(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()
        app.action_show_command_history()
        await pilot.pause()
        assert not app.query(CommandHistoryDialog)


@pytest.mark.asyncio
async def test_alt_h_opens_history_popup(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()
        app.command_history.append("echo hi")
        await pilot.press("alt+h")
        await pilot.pause()
        assert app.query_one(CommandHistoryDialog)


@pytest.mark.asyncio
async def test_history_chip_opens_popup(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()
        app.command_history.append("pwd")
        # the '▲' chip is present to the right of the command line
        assert app.command_line._hist_chip is not None
        # clicking it (posting its message) opens the popup
        app.command_line.post_message(CommandLine.HistoryRequested())
        await pilot.pause()
        assert app.query_one(CommandHistoryDialog)
