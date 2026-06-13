"""LocalProvider wraps scan.py / actions.py behind the VfsProvider contract.

These tests assert the wrapper preserves local-filesystem behaviour and that
it structurally satisfies the protocol — the abstraction must add nothing
observable for ``file`` use.
"""

from dunders.core.vfs import VfsPath
from dunders.core.vfs.provider import VfsProvider
from dunders.fm.vfs_local import LocalProvider


def _provider() -> LocalProvider:
    return LocalProvider()


class TestConformance:
    def test_is_vfs_provider(self):
        assert isinstance(_provider(), VfsProvider)

    def test_scheme_and_capabilities(self):
        p = _provider()
        assert p.scheme == "file"
        assert "read" in p.capabilities and "write" in p.capabilities


class TestScan:
    def test_scan_lists_children(self, tmp_path):
        (tmp_path / "a.txt").write_text("x")
        (tmp_path / "sub").mkdir()
        entries = _provider().scan(VfsPath.local(tmp_path), include_parent=False)
        names = {e.name for e in entries}
        assert names == {"a.txt", "sub"}

    def test_scanned_entries_are_file_scheme(self, tmp_path):
        (tmp_path / "a.txt").write_text("x")
        entries = _provider().scan(VfsPath.local(tmp_path), include_parent=False)
        entry = next(e for e in entries if e.name == "a.txt")
        assert entry.loc.scheme == "file"
        assert entry.path == tmp_path / "a.txt"


class TestStreams:
    def test_write_then_read_round_trip(self, tmp_path):
        p = _provider()
        loc = VfsPath.local(tmp_path / "out.bin")
        with p.open_write(loc) as fh:
            fh.write(b"hello vfs")
        with p.open_read(loc) as fh:
            assert fh.read() == b"hello vfs"


class TestMutations:
    def test_mkdir(self, tmp_path):
        res = _provider().mkdir(VfsPath.local(tmp_path), "newdir")
        assert not res.errors
        assert (tmp_path / "newdir").is_dir()

    def test_copy_within(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_text("payload")
        dest = tmp_path / "dest"
        dest.mkdir()
        res = _provider().copy_within([VfsPath.local(src)], VfsPath.local(dest))
        assert not res.errors
        assert (dest / "src.txt").read_text() == "payload"
        assert src.exists()  # copy keeps the source

    def test_move_within(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_text("payload")
        dest = tmp_path / "dest"
        dest.mkdir()
        res = _provider().move_within([VfsPath.local(src)], VfsPath.local(dest))
        assert not res.errors
        assert (dest / "src.txt").read_text() == "payload"
        assert not src.exists()  # move removes the source

    def test_delete(self, tmp_path):
        victim = tmp_path / "gone.txt"
        victim.write_text("bye")
        res = _provider().delete([VfsPath.local(victim)])
        assert not res.errors
        assert not victim.exists()
