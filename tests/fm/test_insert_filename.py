import shlex

import pytest

from dunders.app import DundersApp


def _cursor_to_name(panel, name) -> bool:
    for i, e in enumerate(panel.entries):
        if e.path.name == name:
            panel.cursor = i
            return True
    return False


def _cursor_to_parent(panel) -> bool:
    for i, e in enumerate(panel.entries):
        if e.is_parent:
            panel.cursor = i
            return True
    return False


@pytest.mark.asyncio
async def test_ctrl_n_on_file_inserts_name(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app._active_panel()
        assert _cursor_to_name(panel, "a.txt")
        app.action_insert_current_file()
        await pilot.pause()
        assert app.command_line.text == "a.txt "


@pytest.mark.asyncio
async def test_ctrl_n_on_folder_inserts_name(tmp_path):
    (tmp_path / "sub").mkdir()
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app._active_panel()
        assert _cursor_to_name(panel, "sub")
        app.action_insert_current_file()
        await pilot.pause()
        assert app.command_line.text == "sub "


@pytest.mark.asyncio
async def test_ctrl_n_on_parent_inserts_cwd_path(tmp_path):
    # cwd is a subdir so a ".." parent row exists.
    work = tmp_path / "work"
    work.mkdir()
    (work / "a.txt").write_text("x")
    app = DundersApp(launch_mode="fm", initial_path=str(work))
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app._active_panel()
        assert _cursor_to_parent(panel)
        app.action_insert_current_file()
        await pilot.pause()
        assert app.command_line.text == str(panel.cwd) + " "


@pytest.mark.asyncio
async def test_ctrl_n_quotes_spaces(tmp_path):
    (tmp_path / "a b.txt").write_text("x")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app._active_panel()
        assert _cursor_to_name(panel, "a b.txt")
        app.action_insert_current_file()
        await pilot.pause()
        assert app.command_line.text == shlex.quote("a b.txt") + " "


@pytest.mark.asyncio
async def test_ctrl_n_noop_when_cursor_out_of_range(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app._active_panel()
        panel.cursor = len(panel.entries)  # deliberately out of range
        before = app.command_line.text
        app.action_insert_current_file()
        await pilot.pause()
        assert app.command_line.text == before
