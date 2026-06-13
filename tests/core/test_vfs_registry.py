"""VfsRegistry routing and the panel's use of it."""

import pytest

from dunders.core.vfs import VfsPath, VfsRegistry
from dunders.fm.file_entry import FileEntry
from dunders.fm.file_panel import FilePanel


class _FakeProvider:
    """Minimal provider that records the locator it was asked to scan."""

    scheme = "file"
    capabilities = frozenset({"read"})

    def __init__(self):
        self.scanned: list[VfsPath] = []

    def scan(self, loc, *, show_hidden=False, include_parent=True):
        self.scanned.append(loc)
        return [FileEntry(loc=loc.child("decoy"), name="decoy", size=0, mtime=0.0, is_dir=False)]


class TestRegistry:
    def test_register_and_resolve(self):
        reg = VfsRegistry()
        p = _FakeProvider()
        reg.register(p)
        loc = VfsPath(scheme="file", root="/", parts=("x",))
        assert reg.resolve(loc) is p
        assert reg.for_scheme("file") is p

    def test_unknown_scheme_raises(self):
        with pytest.raises(KeyError):
            VfsRegistry().for_scheme("zip")

    def test_register_replaces(self):
        reg = VfsRegistry()
        a, b = _FakeProvider(), _FakeProvider()
        reg.register(a)
        reg.register(b)
        assert reg.for_scheme("file") is b


class TestPanelRoutesThroughRegistry:
    def test_refresh_listing_uses_injected_provider(self, tmp_path):
        reg = VfsRegistry()
        fake = _FakeProvider()
        reg.register(fake)
        panel = FilePanel(cwd=tmp_path, registry=reg)
        panel.refresh_listing()
        # The panel scanned via the injected provider, not the real filesystem.
        assert fake.scanned, "provider.scan was not called"
        assert any(e.name == "decoy" for e in panel.entries)

    def test_cwd_is_vfspath_backed(self, tmp_path):
        panel = FilePanel(cwd=tmp_path)
        assert isinstance(panel.cwd_loc, VfsPath)
        assert panel.cwd_loc.scheme == "file"
        assert panel.cwd == tmp_path  # Path shim round-trips

    def test_cwd_setter_rewraps_path(self, tmp_path):
        panel = FilePanel(cwd=tmp_path)
        sub = tmp_path / "sub"
        sub.mkdir()
        panel.cwd = sub
        assert panel.cwd_loc == VfsPath.local(sub)
        assert panel.cwd == sub
