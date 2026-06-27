import pytest

from dunders.app import DundersApp


def _panel_windows(app):
    assert app.desktop is not None
    return [w for w in app.desktop.windows if str(w.id).startswith("panel-")]


@pytest.mark.asyncio
async def test_we_mc_mounts_visible_panels_and_handover(tmp_path):
    # Force suspend mode so no real PTY/tty is needed under pytest.
    app = DundersApp(launch_mode="we-mc", terminal_mode="suspend")
    async with app.run_test() as pilot:
        await pilot.pause()
        panels = _panel_windows(app)
        assert len(panels) == 2
        assert all(p in app.desktop.windows for p in panels)  # visible
        assert app._handover is not None
        # No streaming-console window is auto-created in we-mc.
        assert app._console_default_window is None


def test_we_mc_constructor_stores_terminal_mode():
    app = DundersApp(launch_mode="we-mc", terminal_mode="suspend")
    assert app.launch_mode == "we-mc"
    assert app.terminal_mode == "suspend"


def test_terminal_mode_defaults_to_relay():
    app = DundersApp(launch_mode="fm")
    assert app.terminal_mode == "relay"


def test_is_panel_mode_covers_fm_and_we_mc():
    assert DundersApp(launch_mode="fm")._is_panel_mode() is True
    assert DundersApp(launch_mode="we-mc")._is_panel_mode() is True
    assert DundersApp(launch_mode="editor")._is_panel_mode() is False


class _SpyHandover:
    def __init__(self):
        self.ran = []
        self.screens = 0
        self.shutdown_called = False

    def run_foreground(self, cmd, cwd):
        self.ran.append((cmd, str(cwd)))
        return 0

    def command_screen(self, cwd):
        self.screens += 1

    def shutdown(self):
        self.shutdown_called = True


@pytest.mark.asyncio
async def test_we_mc_command_routes_to_handover(tmp_path):
    app = DundersApp(launch_mode="we-mc", terminal_mode="suspend")
    async with app.run_test() as pilot:
        await pilot.pause()
        spy = _SpyHandover()
        app._handover = spy
        app._run_handover_command("htop")
        assert spy.ran and spy.ran[0][0] == "htop"


@pytest.mark.asyncio
async def test_we_mc_cd_does_not_invoke_handover(tmp_path):
    app = DundersApp(launch_mode="we-mc", terminal_mode="suspend")
    async with app.run_test() as pilot:
        await pilot.pause()
        spy = _SpyHandover()
        app._handover = spy
        app._run_handover_command("cd /")
        assert spy.ran == []  # cd handled inline, no handover


@pytest.mark.asyncio
async def test_we_mc_command_that_cds_moves_active_panel(tmp_path):
    # A typed command that leaves the shell in another directory (the handover
    # reports it via last_cwd) follows the active panel — like User Menu entries.
    sub = tmp_path / "sub"
    sub.mkdir()
    app = DundersApp(launch_mode="we-mc", terminal_mode="suspend",
                  initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        spy = _SpyHandover()
        spy.last_cwd = sub
        app._handover = spy
        app._run_handover_command("pushd sub")
        await pilot.pause()
        assert spy.ran and spy.ran[0][0] == "pushd sub"
        assert app._panel_cwd_for_test().resolve() == sub.resolve()


@pytest.mark.asyncio
async def test_we_mc_command_refused_while_command_suspended(tmp_path):
    # A foreground command (e.g. claude) detached via Ctrl+O is still running in
    # the subshell. Typing a new command must NOT hand it over (its bytes — incl.
    # the cwd-sync `cd` — would be injected into the running child); refuse and
    # tell the user to return via Ctrl+O.
    app = DundersApp(launch_mode="we-mc", terminal_mode="relay")
    async with app.run_test() as pilot:
        await pilot.pause()
        spy = _SpyHandover()
        spy.has_suspended_command = True
        app._handover = spy
        app._run_handover_command("ls")
        assert spy.ran == []  # not handed over while a command is suspended


@pytest.mark.asyncio
async def test_we_mc_ctrl_o_shows_command_screen(tmp_path):
    app = DundersApp(launch_mode="we-mc", terminal_mode="suspend")
    async with app.run_test() as pilot:
        await pilot.pause()
        spy = _SpyHandover()
        app._handover = spy
        app.action_toggle_console()
        assert spy.screens == 1


@pytest.mark.asyncio
async def test_fm_ctrl_o_shows_command_screen(tmp_path):
    # fm mode Ctrl+O drops into the mc-style command screen too: every typed
    # command already routes through the handover, so the old embedded console
    # window would just be empty. Ctrl+O should match we-mc behaviour.
    app = DundersApp(launch_mode="fm", terminal_mode="suspend")
    async with app.run_test() as pilot:
        await pilot.pause()
        spy = _SpyHandover()
        app._handover = spy
        app.action_toggle_console()
        assert spy.screens == 1


@pytest.mark.asyncio
async def test_we_mc_shutdown_called_on_unmount(tmp_path):
    app = DundersApp(launch_mode="we-mc", terminal_mode="suspend")
    async with app.run_test() as pilot:
        await pilot.pause()
        spy = _SpyHandover()
        app._handover = spy
        app.on_unmount()
        assert spy.shutdown_called is True


@pytest.mark.asyncio
async def test_wheel_over_inactive_panel_activates_and_scrolls(tmp_path):
    from dunders.fm.file_panel import FilePanel

    for i in range(10):
        (tmp_path / f"f{i:02d}.txt").write_text("x")
    app = DundersApp(launch_mode="fm", initial_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        panels = list(app.query(FilePanel))
        inactive = next(p for p in panels if not p._is_active_panel)
        # Point the inactive panel at the populated dir so the move is
        # deterministic regardless of where it seeded.
        inactive.cwd = tmp_path
        inactive.refresh_listing()
        before = inactive.cursor
        inactive._wheel(3)
        await pilot.pause()
        assert inactive._is_active_panel  # wheel activated it
        assert inactive.cursor == min(before + 3, len(inactive.entries) - 1)
