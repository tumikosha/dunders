from dunders.app import DundersApp
from dunders.fm.dialogs import InputDialog


async def test_file_menu_has_open_file():
    app = DundersApp(launch_mode="fm", initial_path="/tmp")
    async with app.run_test() as pilot:
        await pilot.pause()
        file_menu = next(m for m in app._all_menus if m.label == "File")
        ids = {getattr(i, "command_id", None) for i in file_menu.items}
        assert "app.open_file" in ids


async def test_open_file_dialog_opens_editor(tmp_path):
    f = tmp_path / "hello.py"
    f.write_text("print('hi')\n")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.action_open_file()
        await pilot.pause()
        dialog = app.query_one(InputDialog)
        dialog.set_value(str(f))
        dialog.action_submit()
        await pilot.pause()
        # An editable editor window was added.
        assert app.desktop is not None
        assert any(
            (w.id or "").startswith("editor-") for w in app.desktop.windows
        ), f"no editor window opened; windows={[w.id for w in app.desktop.windows]}"
