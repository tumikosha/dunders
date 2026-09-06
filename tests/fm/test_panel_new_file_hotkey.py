"""Shift+F4 in a file panel opens the New file dialog (File ▸ New)."""

from dunders.app import DundersApp
from dunders.fm.dialogs import NewFileDialog


async def test_shift_f4_opens_the_new_file_dialog(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()
        assert not app.query(NewFileDialog)

        await pilot.press("shift+f4")
        await pilot.pause()

        dialog = app.query_one(NewFileDialog)
        # Same dialog the menu item opens: seeded with the active panel's cwd.
        assert str(tmp_path) in dialog.context.parent.as_posix() or \
            dialog.context.parent == tmp_path


async def test_panel_new_command_declares_the_hotkey(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        panel = app._active_panel()
        cmd = next(c for c in panel.get_commands() if c.id == "panel.new")
        assert cmd.hotkey == "shift+f4"
