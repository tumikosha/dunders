import pytest
from textual.app import App, ComposeResult

from tyui.fm.commandline import CommandLine


class _Harness(App):
    def __init__(self) -> None:
        super().__init__()
        self.received: list[str] = []

    def compose(self) -> ComposeResult:
        yield CommandLine(id="cmdline")

    def on_command_line_submitted(self, event: CommandLine.Submitted) -> None:
        self.received.append(event.text)


@pytest.mark.asyncio
async def test_commandline_emits_submitted_message_on_enter():
    app = _Harness()
    async with app.run_test() as pilot:
        cmd = app.query_one("#cmdline", CommandLine)
        cmd.set_text("ls -la")
        cmd.submit()
        await pilot.pause()
        assert app.received == ["ls -la"]


@pytest.mark.asyncio
async def test_commandline_clears_text_after_submit():
    app = _Harness()
    async with app.run_test() as pilot:
        cmd = app.query_one("#cmdline", CommandLine)
        cmd.set_text("foo")
        cmd.submit()
        await pilot.pause()
        assert cmd.text == ""
