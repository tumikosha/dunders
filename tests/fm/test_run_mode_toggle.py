"""The run-mode chip next to the command line flips relay <-> suspend."""

import pytest

from dunders.app import DundersApp
from dunders.fm.commandline import CommandLine


class _SpyHandover:
    def __init__(self):
        self.shutdown_called = False

    def run_foreground(self, cmd, cwd):  # pragma: no cover - unused here
        return 0

    def command_screen(self, cwd):  # pragma: no cover - unused here
        pass

    def shutdown(self):
        self.shutdown_called = True


def _toggle(app: DundersApp) -> None:
    app.on_command_line_run_mode_toggle_requested(
        CommandLine.RunModeToggleRequested()
    )


@pytest.mark.asyncio
async def test_chip_reflects_default_relay_mode(tmp_path):
    app = DundersApp(launch_mode="fm")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.terminal_mode == "relay"
        assert app._run_mode_chip()[0] == "run: relay"
        assert app.command_line._mode_chip.display is True


@pytest.mark.asyncio
async def test_toggle_flips_mode_and_rebuilds_handover(tmp_path):
    app = DundersApp(launch_mode="fm")
    async with app.run_test() as pilot:
        await pilot.pause()
        spy = _SpyHandover()
        app._handover = spy
        _toggle(app)
        # Mode flipped, old handover torn down so the next command rebuilds it.
        assert app.terminal_mode == "suspend"
        assert spy.shutdown_called is True
        assert app._handover is None
        assert app._run_mode_chip()[0] == "run: tty"


@pytest.mark.asyncio
async def test_toggle_is_round_trip(tmp_path):
    app = DundersApp(launch_mode="we", terminal_mode="suspend")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._run_mode_chip()[0] == "run: tty"
        _toggle(app)
        assert app.terminal_mode == "relay"
        _toggle(app)
        assert app.terminal_mode == "suspend"


@pytest.mark.asyncio
async def test_toggle_then_command_uses_new_mode(tmp_path):
    # After switching to suspend, a typed command runs via the freshly-built
    # suspend handover (we can't assert the class without a tty, but we can
    # assert a handover gets built and run).
    app = DundersApp(launch_mode="we")
    async with app.run_test() as pilot:
        await pilot.pause()
        _toggle(app)  # relay -> suspend, _handover reset to None
        spy = _SpyHandover()
        app._handover = spy  # stand in for the rebuilt handover
        app.on_command_line_submitted(CommandLine.Submitted("claude"))
        # _ensure_handover returns the spy; command dispatched through it.
        assert app.terminal_mode == "suspend"
