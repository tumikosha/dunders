"""Graceful guard: file ops (F3/F4/F5/F6/F7/F8, Enter) inside a browsed zip
warn instead of crashing — entry.path would raise on a zip-scheme locator.
"""

import zipfile
from pathlib import Path

import pytest

from dunders.app import DundersApp
from dunders.fm.file_panel import FilePanel
from dunders.fm.viewer import ViewerContent
from dunders.windowing import Desktop
from dunders.windowing.editor import EditorContent


def _make_archive(dirpath: Path) -> Path:
    path = dirpath / "a.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("inside.txt", b"hello")
    return path


def _active_panel(app: DundersApp) -> FilePanel:
    panel = app._active_panel()
    assert panel is not None
    return panel


def _enter_zip(panel: FilePanel) -> None:
    panel.refresh_listing()
    panel.cursor = next(i for i, e in enumerate(panel.entries) if e.name == "a.zip")
    panel.activate()
    assert panel.cwd_loc.scheme == "zip"


def _editor_count(app: DundersApp) -> int:
    desktop = app.query_one(Desktop)
    return sum(isinstance(w.content, EditorContent) for w in desktop.windows)


class TestEntryGuard:
    def test_is_local_entry_helper(self, tmp_path):
        archive = _make_archive(tmp_path)
        from dunders.fm.providers.zip_provider import ZipProvider
        from dunders.core.vfs import VfsPath

        zip_entry = ZipProvider().scan(
            VfsPath(scheme="zip", root=str(archive), parts=()), include_parent=False
        )[0]
        assert DundersApp._is_local_entry(zip_entry) is False


def _viewers(app: DundersApp) -> list:
    desktop = app.query_one(Desktop)
    return [w for w in desktop.windows if isinstance(w.content, ViewerContent)]


@pytest.mark.asyncio
async def test_f3_view_inside_zip_opens_readonly_viewer(tmp_path):
    _make_archive(tmp_path)  # inside.txt = b"hello"
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = _active_panel(app)
        _enter_zip(panel)
        panel.cursor = next(i for i, e in enumerate(panel.entries) if e.name == "inside.txt")
        app.action_view()  # reads the member through the VFS provider
        await pilot.pause()
        viewers = _viewers(app)
        assert len(viewers) == 1
        # The viewer shows the member's decoded content.
        assert "\n".join(viewers[0].content._buffer.lines) == "hello"


@pytest.mark.asyncio
async def test_f3_view_binary_member_declined(tmp_path):
    path = tmp_path / "a.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("bin.dat", b"\x00\x01\x02BIN")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = _active_panel(app)
        _enter_zip(panel)
        panel.cursor = next(i for i, e in enumerate(panel.entries) if e.name == "bin.dat")
        app.action_view()  # must not raise, must not open a viewer
        await pilot.pause()
        assert _viewers(app) == []


@pytest.mark.asyncio
async def test_f4_edit_inside_zip_does_not_open_editor(tmp_path):
    _make_archive(tmp_path)
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = _active_panel(app)
        _enter_zip(panel)
        panel.cursor = next(i for i, e in enumerate(panel.entries) if e.name == "inside.txt")
        before = _editor_count(app)
        app.action_edit()  # must not raise
        await pilot.pause()
        assert _editor_count(app) == before


@pytest.mark.asyncio
async def test_f7_mkdir_inside_zip_is_blocked(tmp_path):
    _make_archive(tmp_path)
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = _active_panel(app)
        _enter_zip(panel)
        app.action_mkdir()  # must not raise, must not create a dir next to the zip
        await pilot.pause()
        assert not app._has_active_modal()  # dialog was not opened
        # No stray directory created alongside the archive.
        assert {p.name for p in tmp_path.iterdir()} == {"a.zip"}
