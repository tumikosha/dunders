"""transfer() — the VFS copy/move dispatcher.

Intra-provider transfers must behave exactly like the old copy_paths /
move_paths; cross-provider transfers are an explicit not-yet boundary.
"""

import zipfile

from dunders.core.vfs import VfsPath
from dunders.fm.vfs_engine import transfer
from dunders.fm.vfs_local import default_registry


def _reg():
    return default_registry()


class TestIntraProvider:
    def test_copy(self, tmp_path):
        src = tmp_path / "a.txt"
        src.write_text("payload")
        dest = tmp_path / "dest"
        dest.mkdir()
        res = transfer(
            _reg(), [VfsPath.local(src)], VfsPath.local(dest), mode="copy"
        )
        assert not res.errors
        assert (dest / "a.txt").read_text() == "payload"
        assert src.exists()  # copy keeps source

    def test_move(self, tmp_path):
        src = tmp_path / "a.txt"
        src.write_text("payload")
        dest = tmp_path / "dest"
        dest.mkdir()
        res = transfer(
            _reg(), [VfsPath.local(src)], VfsPath.local(dest), mode="move"
        )
        assert not res.errors
        assert (dest / "a.txt").read_text() == "payload"
        assert not src.exists()  # move removes source

    def test_copy_with_rename(self, tmp_path):
        src = tmp_path / "a.txt"
        src.write_text("payload")
        dest = tmp_path / "dest"
        dest.mkdir()
        res = transfer(
            _reg(), [VfsPath.local(src)], VfsPath.local(dest),
            mode="copy", rename_to="renamed.txt",
        )
        assert not res.errors
        assert (dest / "renamed.txt").read_text() == "payload"

    def test_progress_called(self, tmp_path):
        src = tmp_path / "a.txt"
        src.write_text("x")
        dest = tmp_path / "dest"
        dest.mkdir()
        seen: list[tuple[int, int]] = []
        transfer(
            _reg(), [VfsPath.local(src)], VfsPath.local(dest),
            mode="copy", on_progress=lambda i, n: seen.append((i, n)),
        )
        assert seen  # provider forwarded the progress callback


class TestBoundaries:
    def test_empty_sources_is_noop(self, tmp_path):
        res = transfer(_reg(), [], VfsPath.local(tmp_path), mode="copy")
        assert not res.errors and not res.succeeded


def _make_zip(path):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("top.txt", b"hello")
        zf.writestr("dir/inner.txt", b"world")
        zf.writestr("dir/sub/deep.txt", b"deep")
    return path


class TestCrossProviderExtraction:
    def test_extract_single_member_zip_to_local(self, tmp_path):
        archive = _make_zip(tmp_path / "a.zip")
        dest = tmp_path / "out"
        dest.mkdir()
        src = VfsPath(scheme="zip", root=str(archive), parts=("top.txt",))
        res = transfer(_reg(), [src], VfsPath.local(dest), mode="copy")
        assert not res.errors
        assert (dest / "top.txt").read_bytes() == b"hello"
        assert res.succeeded == [dest / "top.txt"]

    def test_extract_directory_recursively(self, tmp_path):
        archive = _make_zip(tmp_path / "a.zip")
        dest = tmp_path / "out"
        dest.mkdir()
        src = VfsPath(scheme="zip", root=str(archive), parts=("dir",))
        res = transfer(_reg(), [src], VfsPath.local(dest), mode="copy")
        assert not res.errors
        assert (dest / "dir" / "inner.txt").read_bytes() == b"world"
        assert (dest / "dir" / "sub" / "deep.txt").read_bytes() == b"deep"

    def test_extract_with_rename(self, tmp_path):
        archive = _make_zip(tmp_path / "a.zip")
        dest = tmp_path / "out"
        dest.mkdir()
        src = VfsPath(scheme="zip", root=str(archive), parts=("top.txt",))
        transfer(
            _reg(), [src], VfsPath.local(dest), mode="copy", rename_to="renamed.txt"
        )
        assert (dest / "renamed.txt").read_bytes() == b"hello"

    def test_progress_reported(self, tmp_path):
        archive = _make_zip(tmp_path / "a.zip")
        dest = tmp_path / "out"
        dest.mkdir()
        src = VfsPath(scheme="zip", root=str(archive), parts=("dir",))
        seen: list[tuple[int, int]] = []
        transfer(
            _reg(), [src], VfsPath.local(dest), mode="copy",
            on_progress=lambda i, n: seen.append((i, n)),
        )
        assert seen[-1] == (2, 2)  # dir/ has two files (inner.txt, sub/deep.txt)

    def test_move_out_of_readonly_zip_copies_then_reports(self, tmp_path):
        archive = _make_zip(tmp_path / "a.zip")
        dest = tmp_path / "out"
        dest.mkdir()
        src = VfsPath(scheme="zip", root=str(archive), parts=("top.txt",))
        res = transfer(_reg(), [src], VfsPath.local(dest), mode="move")
        # The copy lands; the source can't be removed from a read-only zip.
        assert (dest / "top.txt").read_bytes() == b"hello"
        assert len(res.errors) == 1
        assert "not removed" in res.errors[0].reason
