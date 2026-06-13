"""ZipProvider — browse a zip archive as a directory tree (read-only)."""

import zipfile

import pytest

from dunders.core.vfs import VfsPath
from dunders.core.vfs.provider import VfsProvider
from dunders.fm.providers.zip_provider import ZipProvider
from dunders.fm.vfs_local import default_registry


def _make_zip(path, files: dict[str, bytes]):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return path


@pytest.fixture
def archive(tmp_path):
    return _make_zip(
        tmp_path / "a.zip",
        {
            "top.txt": b"hello",
            "dir/inner.txt": b"world",
            "dir/sub/deep.txt": b"deep",
        },
    )


def _root(archive) -> VfsPath:
    return VfsPath(scheme="zip", root=str(archive), parts=())


class TestConformance:
    def test_is_vfs_provider(self):
        assert isinstance(ZipProvider(), VfsProvider)

    def test_registered_in_default_registry(self):
        assert default_registry().for_scheme("zip") is not None


class TestScan:
    def test_root_lists_top_level_and_synthesised_dir(self, archive):
        entries = ZipProvider().scan(_root(archive), include_parent=False)
        by_name = {e.name: e for e in entries}
        assert set(by_name) == {"top.txt", "dir"}
        assert by_name["dir"].is_dir is True  # synthesised, no explicit entry
        assert by_name["top.txt"].is_dir is False
        assert by_name["top.txt"].size == 5  # "hello"

    def test_child_loc_is_zip_scheme(self, archive):
        entries = ZipProvider().scan(_root(archive), include_parent=False)
        top = next(e for e in entries if e.name == "top.txt")
        assert top.loc.scheme == "zip"
        assert top.loc.parts == ("top.txt",)

    def test_descend_into_subdir(self, archive):
        dir_loc = VfsPath(scheme="zip", root=str(archive), parts=("dir",))
        names = {e.name for e in ZipProvider().scan(dir_loc, include_parent=False)}
        assert names == {"inner.txt", "sub"}

    def test_nested_subdir(self, archive):
        sub = VfsPath(scheme="zip", root=str(archive), parts=("dir", "sub"))
        names = {e.name for e in ZipProvider().scan(sub, include_parent=False)}
        assert names == {"deep.txt"}


class TestParentEntry:
    def test_parent_at_root_exits_to_local_dir(self, archive):
        entries = ZipProvider().scan(_root(archive), include_parent=True)
        parent = next(e for e in entries if e.name == "..")
        # Leaving the archive lands in the local folder that holds the .zip.
        assert parent.loc.scheme == "file"
        assert parent.loc == VfsPath.local(archive.parent)

    def test_parent_in_subdir_goes_up_within_zip(self, archive):
        dir_loc = VfsPath(scheme="zip", root=str(archive), parts=("dir",))
        entries = ZipProvider().scan(dir_loc, include_parent=True)
        parent = next(e for e in entries if e.name == "..")
        assert parent.loc.scheme == "zip"
        assert parent.loc.parts == ()


class TestRead:
    def test_open_read_member(self, archive):
        loc = VfsPath(scheme="zip", root=str(archive), parts=("dir", "inner.txt"))
        with ZipProvider().open_read(loc) as fh:
            assert fh.read() == b"world"


class TestReadOnly:
    def test_open_write_raises(self, archive):
        with pytest.raises(OSError):
            ZipProvider().open_write(_root(archive))

    def test_mkdir_raises(self, archive):
        with pytest.raises(OSError):
            ZipProvider().mkdir(_root(archive), "x")

    def test_copy_within_returns_none(self, archive):
        # No intra-zip fast path: extraction is a cross-provider transfer.
        assert ZipProvider().copy_within([_root(archive)], _root(archive)) is None


class TestCache:
    def test_index_cached_until_archive_changes(self, archive):
        p = ZipProvider()
        first = p._index_for(_root(archive))
        assert p._index_for(_root(archive)) is first  # same object, cache hit
