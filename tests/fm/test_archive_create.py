"""App-level: Create archive (F-menu action_pack) and the F5 'zip:' overload."""

import zipfile
from pathlib import Path

import pytest

from dunders.app import DundersApp
from dunders.fm.dialogs import CopyMoveDialog, NewFileDialog
from dunders.fm.file_panel import FilePanel
from dunders.windowing import Desktop, Window


def _seed(dirpath: Path) -> None:
    (dirpath / "a.txt").write_text("aaa")
    (dirpath / "dir").mkdir()
    (dirpath / "dir" / "inner.txt").write_text("inner")


def _panels(app: DundersApp):
    desktop = app.query_one(Desktop)
    left = desktop.query_one("#panel-left", Window).content
    right = desktop.query_one("#panel-right", Window).content
    assert isinstance(left, FilePanel) and isinstance(right, FilePanel)
    return left, right


def _cursor_on(panel: FilePanel, name: str) -> None:
    panel.cursor = next(i for i, e in enumerate(panel.entries) if e.name == name)


async def _settle(pilot):
    # pack/copy run on a worker thread; give it a few ticks to finish.
    for _ in range(20):
        await pilot.pause()


@pytest.mark.asyncio
async def test_action_pack_creates_archive_from_selection(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _seed(src)
    app = DundersApp(launch_mode="fm", initial_path=str(src))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        left, _ = _panels(app)
        _cursor_on(left, "a.txt")
        app.action_pack()
        await pilot.pause()
        dialog = app.query_one(NewFileDialog)
        dialog._input.value = "bundle.zip"
        dialog.action_submit()
        await _settle(pilot)
        out = src / "bundle.zip"
        assert out.exists()
        with zipfile.ZipFile(out) as zf:
            assert zf.namelist() == ["a.txt"]
            assert zf.read("a.txt") == b"aaa"


@pytest.mark.asyncio
async def test_action_pack_refused_inside_archive(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _seed(src)
    # Make an archive to browse into.
    with zipfile.ZipFile(src / "z.zip", "w") as zf:
        zf.writestr("m.txt", b"hi")
    app = DundersApp(launch_mode="fm", initial_path=str(src))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        left, _ = _panels(app)
        _cursor_on(left, "z.zip")
        left.activate()
        await pilot.pause()
        assert left.cwd_loc.scheme == "zip"
        app.action_pack()  # must not raise, must not open a dialog
        await pilot.pause()
        assert not list(app.query(NewFileDialog))


@pytest.mark.asyncio
async def test_f5_zip_prefix_packs_into_archive(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _seed(src)
    dst = tmp_path / "dst"
    dst.mkdir()
    app = DundersApp(launch_mode="fm", initial_path=str(src))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        left, right = _panels(app)
        right.cwd = dst
        right.refresh_listing()
        _cursor_on(left, "dir")
        await pilot.press("f5")
        await pilot.pause()
        dialog = app.query_one(CopyMoveDialog)
        # Destination prefixed with "zip:" → pack instead of copy.
        dialog._input.value = "zip:packed.zip"
        dialog.action_submit()
        await _settle(pilot)
        out = dst / "packed.zip"
        assert out.exists()
        with zipfile.ZipFile(out) as zf:
            assert zf.read("dir/inner.txt") == b"inner"
