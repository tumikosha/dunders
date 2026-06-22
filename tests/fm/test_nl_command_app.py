"""App-level NL→command flow: trigger detection, suggestion dialog, edit."""

import pytest

from dunders.ai.types import ChatResponse
from dunders.app import DundersApp
from dunders.fm.commandline import CommandLine
from dunders.fm.nl_command import NlCommandDialog


def test_nl_intent_detection(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    assert app._nl_intent("# find files") == "find files"
    assert app._nl_intent("?how to untar") == "how to untar"
    assert app._nl_intent("ls -la") is None          # plain command
    assert app._nl_intent("   ") is None
    app._ai_cmd_mode = True
    assert app._nl_intent("list files") == "list files"  # mode on → NL


@pytest.mark.asyncio
async def test_toggle_ai_mode_updates_hint(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        assert app._ai_cmd_mode is False
        app.action_toggle_ai_cmd_mode()
        assert app._ai_cmd_mode is True
        app.action_toggle_ai_cmd_mode()
        assert app._ai_cmd_mode is False


@pytest.mark.asyncio
async def test_prefix_opens_suggestion_dialog(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        async def fake_chat(messages, **kw):
            return ChatResponse(text="CMD: ls -la\nWHY: lists files")

        app.ai.chat = fake_chat
        app.on_command_line_submitted(CommandLine.Submitted("# list files"))
        for _ in range(6):
            await pilot.pause()
        dialog = app.query_one(NlCommandDialog)
        assert dialog._command == "ls -la"
        assert dialog._why == "lists files"
        assert dialog._run_btn.has_focus  # Run is focused on open
        # the NL line as typed is recorded in the command history
        assert "# list files" in app.command_history.entries()


@pytest.mark.asyncio
async def test_edit_drops_command_into_command_line(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        app._nl_edit_command("du -sh *")
        await pilot.pause()
        assert app.command_line.text == "du -sh *"


@pytest.mark.asyncio
async def test_ai_failure_does_not_crash(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        async def boom(messages, **kw):
            from dunders.ai.provider import ProviderUnavailable

            raise ProviderUnavailable("no provider")

        app.ai.chat = boom
        app.on_command_line_submitted(CommandLine.Submitted("# anything"))
        for _ in range(6):
            await pilot.pause()
        # no dialog, no crash
        assert not app.query(NlCommandDialog)
