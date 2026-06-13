"""F5 extraction: copy a member out of a browsed zip into the opposite
(local) panel via the cross-provider transfer engine.
"""

import zipfile
from pathlib import Path

import pytest

from dunders.app import DundersApp
from dunders.fm.dialogs import CopyMoveDialog
from dunders.fm.file_panel import FilePanel
from dunders.windowing import Desktop, Window


def _make_archive(dirpath: Path) -> Path:
    path = dirpath / "a.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("top.txt", b"hello")
        zf.writestr("dir/inner.txt", b"world")
        zf.writestr("dir/sub/deep.txt", b"deep")
    return path


def _panels(app: DundersApp):
    desktop = app.query_one(Desktop)
    left = desktop.query_one("#panel-left", Window).content
    right = desktop.query_one("#panel-right", Window).content
    assert isinstance(left, FilePanel) and isinstance(right, FilePanel)
    return left, right


def _cursor_on(panel: FilePanel, name: str) -> None:
    panel.cursor = next(i for i, e in enumerate(panel.entries) if e.name == name)


@pytest.mark.asyncio
async def test_f5_extracts_zip_member_to_opposite_panel(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _make_archive(src)
    dst = tmp_path / "dst"
    dst.mkdir()

    app = DundersApp(launch_mode="fm", initial_path=str(src))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        left, right = _panels(app)
        right.cwd = dst
        right.refresh_listing()
        # Enter the archive in the active (left) panel.
        _cursor_on(left, "a.zip")
        left.activate()
        await pilot.pause()
        assert left.cwd_loc.scheme == "zip"
        # F5 the member; submit the dialog (prefilled with dst/top.txt).
        _cursor_on(left, "top.txt")
        await pilot.press("f5")
        await pilot.pause()
        app.query_one(CopyMoveDialog).action_submit()
        await pilot.pause()
        assert (dst / "top.txt").read_bytes() == b"hello"


@pytest.mark.asyncio
async def test_f5_extracts_directory_recursively(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _make_archive(src)
    dst = tmp_path / "dst"
    dst.mkdir()

    app = DundersApp(launch_mode="fm", initial_path=str(src))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        left, right = _panels(app)
        right.cwd = dst
        right.refresh_listing()
        _cursor_on(left, "a.zip")
        left.activate()
        await pilot.pause()
        _cursor_on(left, "dir")
        await pilot.press("f5")
        await pilot.pause()
        app.query_one(CopyMoveDialog).action_submit()
        await pilot.pause()
        assert (dst / "dir" / "inner.txt").read_bytes() == b"world"
        assert (dst / "dir" / "sub" / "deep.txt").read_bytes() == b"deep"


@pytest.mark.asyncio
async def test_f5_into_archive_is_blocked(tmp_path):
    # Left = local file; right browses a zip. Copy INTO an archive is refused.
    src = tmp_path / "src"
    src.mkdir()
    (src / "thing.txt").write_text("hi")
    _make_archive(src)  # also gives left an a.zip, irrelevant here

    app = DundersApp(launch_mode="fm", initial_path=str(src))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        left, right = _panels(app)
        # Put the right panel inside the archive.
        _cursor_on(right, "a.zip")
        right.activate()
        await pilot.pause()
        assert right.cwd_loc.scheme == "zip"
        # Active (left) panel has a real file under the cursor.
        _cursor_on(left, "thing.txt")
        await pilot.press("f5")
        await pilot.pause()
        # Refused: no copy dialog opened (can't write into a zip).
        assert not list(app.query(CopyMoveDialog))
