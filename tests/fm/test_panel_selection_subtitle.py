"""The panel's bottom border reports how many files/folders are selected."""

from dunders.app import DundersApp
from dunders.fm.file_panel import FilePanel
from dunders.windowing import Window


def _panel(app: DundersApp) -> FilePanel:
    return app.desktop.query_one("#panel-left", Window).content


async def _seeded(tmp_path):
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    (tmp_path / "dir1").mkdir()
    (tmp_path / "dir2").mkdir()
    return DundersApp(launch_mode="fm", initial_path=str(tmp_path))


def _select(panel: FilePanel, *names: str) -> None:
    for entry in panel.entries:
        if entry.loc.name in names:
            panel.selection.add(entry.loc)
    panel._update_subtitle()


async def test_no_selection_leaves_the_bottom_border_clean(tmp_path):
    app = await _seeded(tmp_path)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()
        assert _panel(app).window_subtitle is None


async def test_subtitle_counts_files_and_folders(tmp_path):
    app = await _seeded(tmp_path)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()
        panel = _panel(app)

        _select(panel, "a.txt", "b.txt", "dir1")
        assert panel.window_subtitle == "Selected: 2 files, 1 folder"

        _select(panel, "dir2")
        assert panel.window_subtitle == "Selected: 2 files, 2 folders"


async def test_subtitle_uses_the_singular_and_omits_an_empty_half(tmp_path):
    app = await _seeded(tmp_path)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()
        panel = _panel(app)

        _select(panel, "a.txt")
        assert panel.window_subtitle == "Selected: 1 file"

        panel.clear_selection()
        _select(panel, "dir1")
        assert panel.window_subtitle == "Selected: 1 folder"


async def test_clearing_and_toggling_keep_the_border_in_step(tmp_path):
    app = await _seeded(tmp_path)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()
        panel = _panel(app)
        panel.focus()
        await pilot.pause()

        # Insert selects the entry under the cursor and advances.
        panel.cursor = next(i for i, e in enumerate(panel.entries)
                            if e.loc.name == "a.txt")
        panel.toggle_selection()
        await pilot.pause()
        assert panel.window_subtitle == "Selected: 1 file"

        panel.clear_selection()
        await pilot.pause()
        assert panel.window_subtitle is None


async def test_subtitle_reaches_the_window_decorations(tmp_path):
    app = await _seeded(tmp_path)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()
        win = app.desktop.query_one("#panel-left", Window)

        _select(win.content, "a.txt", "dir1")
        await pilot.pause()
        assert win.decorations.subtitle == "Selected: 1 file, 1 folder"
