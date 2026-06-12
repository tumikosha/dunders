"""Key Probe diagnostic window: opens via command and logs incoming keys."""

from dunders.app import DundersApp
from dunders.fm.key_probe import KeyProbeContent


async def test_key_probe_command_opens_window():
    app = DundersApp(launch_mode="fm")
    async with app.run_test() as pilot:
        assert app.dispatcher is not None
        app.dispatcher.dispatch("help.key_probe")
        await pilot.pause()
        probes = [
            w for w in app.desktop.windows
            if isinstance(w.content, KeyProbeContent)
        ]
        assert len(probes) == 1


async def test_key_probe_logs_keypress():
    app = DundersApp(launch_mode="fm")
    async with app.run_test() as pilot:
        assert app.dispatcher is not None
        app.dispatcher.dispatch("help.key_probe")
        await pilot.pause()
        probe = next(
            w.content for w in app.desktop.windows
            if isinstance(w.content, KeyProbeContent)
        )
        # Probe grabs focus on mount; feed a key and confirm it was logged.
        await pilot.press("a")
        await pilot.pause()
        assert probe._log is not None
        assert probe._log.lines, "expected the key probe log to record a keypress"
