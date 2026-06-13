"""Async listing for slow (network) providers: a loading row appears
immediately, the scan runs off the UI thread, and a result is discarded if a
newer navigation supersedes it.
"""

import threading

import pytest

from dunders.app import DundersApp
from dunders.core.vfs import VfsPath
from dunders.fm.file_entry import FileEntry


class _SlowProvider:
    """A 'slow' provider whose scan blocks on a gate until the test releases it."""

    scheme = "slow"
    capabilities = frozenset({"read", "slow"})

    def __init__(self):
        self.gate = threading.Event()
        self.scans = 0

    def scan(self, loc, *, show_hidden=False, include_parent=True):
        self.scans += 1
        self.gate.wait(timeout=5)
        return [FileEntry(loc=loc.child("remote.txt"), name="remote.txt",
                          size=1, mtime=0.0, is_dir=False)]


def _active(app):
    return app._active_panel()


async def _pump(pilot, n=30):
    for _ in range(n):
        await pilot.pause()


@pytest.mark.asyncio
async def test_slow_scan_shows_loading_then_applies(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await _pump(pilot, 2)
        panel = _active(app)
        slow = _SlowProvider()
        app._vfs_registry.register(slow)
        panel._change_cwd_loc(VfsPath(scheme="slow", root="x", parts=()))
        await pilot.pause()
        # Loading shown immediately; the scan is dispatched, not yet applied.
        assert panel._loading is True
        assert any("loading" in e.name for e in panel.entries)
        assert slow.scans == 1
        # Release the scan → result is applied on the UI thread.
        slow.gate.set()
        await _pump(pilot)
        assert panel._loading is False
        assert {e.name for e in panel.entries if not e.is_parent} == {"remote.txt"}


@pytest.mark.asyncio
async def test_stale_slow_scan_is_discarded(tmp_path):
    (tmp_path / "local.txt").write_text("x")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await _pump(pilot, 2)
        panel = _active(app)
        slow = _SlowProvider()
        app._vfs_registry.register(slow)
        # Start a slow scan (blocks), then navigate away to a local dir before
        # it returns.
        panel._change_cwd_loc(VfsPath(scheme="slow", root="x", parts=()))
        await pilot.pause()
        assert panel._loading is True
        panel._change_cwd_loc(VfsPath.local(tmp_path))  # sync, applies at once
        await pilot.pause()
        assert panel._loading is False
        assert any(e.name == "local.txt" for e in panel.entries)
        # The late slow result must be discarded (token mismatch).
        slow.gate.set()
        await _pump(pilot)
        assert any(e.name == "local.txt" for e in panel.entries)
        assert not any(e.name == "remote.txt" for e in panel.entries)


def test_local_provider_stays_synchronous(tmp_path):
    """A fast provider must NOT go async — refresh_listing populates at once
    (no app needed), so existing sync callers keep working."""
    from dunders.fm.file_panel import FilePanel

    (tmp_path / "a.txt").write_text("a")
    panel = FilePanel(cwd=tmp_path)
    panel.refresh_listing()
    assert panel._loading is False
    assert any(e.name == "a.txt" for e in panel.entries)
