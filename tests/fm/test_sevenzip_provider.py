"""SevenZipProvider — listing parser (binary-free) + integration via the 7z CLI."""

import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

from dunders.core.vfs import VfsPath
from dunders.core.vfs.provider import VfsProvider
from dunders.fm.providers.sevenzip_provider import (
    SevenZipProvider,
    _build_index,
    _parse_listing,
    find_7z,
)
from dunders.fm.vfs_engine import transfer
from dunders.fm.vfs_local import default_registry

_HAS_7Z = find_7z() is not None
_needs_7z = pytest.mark.skipif(not _HAS_7Z, reason="no 7z binary on PATH")


# ---- parser (no binary needed) -------------------------------------------

_SAMPLE = dedent("""\
    7-Zip [64] 17.05

    Listing archive: t.7z

    --
    Path = t.7z
    Type = 7z

    ----------
    Path = dir
    Size = 0
    Modified = 2026-06-13 12:20:12
    Attributes = D_ drwxr-xr-x

    Path = a.txt
    Size = 6
    Modified = 2026-06-13 12:20:12
    Attributes = A_ -rw-r--r--

    Path = dir/inner.txt
    Size = 5
    Modified = 2026-06-13 12:20:12
    Attributes = A_ -rw-r--r--
""")


class TestParser:
    def test_parse_listing_records(self):
        recs = _parse_listing(_SAMPLE)
        by_path = {"/".join(p): (is_dir, size) for p, is_dir, size, _ in recs}
        assert by_path["dir"] == (True, 0)
        assert by_path["a.txt"] == (False, 6)
        assert by_path["dir/inner.txt"] == (False, 5)
        # the header "Path = t.7z" (before the ---- line) is not a member
        assert "t.7z" not in by_path

    def test_build_index_tree(self):
        index = _build_index(_parse_listing(_SAMPLE))
        root = {n.name: n for n in index[()]}
        assert set(root) == {"dir", "a.txt"}
        assert root["dir"].is_dir is True
        child = {n.name for n in index[("dir",)]}
        assert child == {"inner.txt"}


# ---- integration (requires the real 7z CLI) ------------------------------


def _make_7z(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    (work / "a.txt").write_text("hello")
    (work / "dir" / "sub").mkdir(parents=True)
    (work / "dir" / "sub" / "deep.txt").write_text("deep")
    archive = tmp_path / "test.7z"
    subprocess.run(
        [find_7z(), "a", str(archive), "a.txt", "dir"],
        cwd=work, capture_output=True, check=True,
    )
    return archive


def _root(archive: Path) -> VfsPath:
    return VfsPath(scheme="7z", root=str(archive), parts=())


class TestConformance:
    def test_is_vfs_provider(self):
        assert isinstance(SevenZipProvider(binary="/bin/true"), VfsProvider)

    @_needs_7z
    def test_registered_when_binary_present(self):
        assert "7z" in default_registry().schemes()


@_needs_7z
class TestBrowse:
    def test_scan_root(self, tmp_path):
        archive = _make_7z(tmp_path)
        names = {e.name for e in SevenZipProvider().scan(_root(archive), include_parent=False)}
        assert names == {"a.txt", "dir"}

    def test_descend(self, tmp_path):
        archive = _make_7z(tmp_path)
        sub = VfsPath(scheme="7z", root=str(archive), parts=("dir", "sub"))
        names = {e.name for e in SevenZipProvider().scan(sub, include_parent=False)}
        assert names == {"deep.txt"}

    def test_is_dir(self, tmp_path):
        archive = _make_7z(tmp_path)
        p = SevenZipProvider()
        assert p.is_dir(VfsPath(scheme="7z", root=str(archive), parts=("dir",)))
        assert not p.is_dir(VfsPath(scheme="7z", root=str(archive), parts=("a.txt",)))

    def test_open_read_member(self, tmp_path):
        archive = _make_7z(tmp_path)
        loc = VfsPath(scheme="7z", root=str(archive), parts=("a.txt",))
        with SevenZipProvider().open_read(loc) as fh:
            assert fh.read() == b"hello"

    def test_parent_at_root_exits_to_local_dir(self, tmp_path):
        archive = _make_7z(tmp_path)
        entries = SevenZipProvider().scan(_root(archive), include_parent=True)
        parent = next(e for e in entries if e.name == "..")
        assert parent.loc == VfsPath.local(archive.parent)


@_needs_7z
class TestExtract:
    def test_extract_member_to_local(self, tmp_path):
        archive = _make_7z(tmp_path)
        dest = tmp_path / "out"
        dest.mkdir()
        src = VfsPath(scheme="7z", root=str(archive), parts=("a.txt",))
        res = transfer(default_registry(), [src], VfsPath.local(dest), mode="copy")
        assert not res.errors
        assert (dest / "a.txt").read_bytes() == b"hello"

    def test_extract_directory_recursively(self, tmp_path):
        archive = _make_7z(tmp_path)
        dest = tmp_path / "out"
        dest.mkdir()
        src = VfsPath(scheme="7z", root=str(archive), parts=("dir",))
        res = transfer(default_registry(), [src], VfsPath.local(dest), mode="copy")
        assert not res.errors
        assert (dest / "dir" / "sub" / "deep.txt").read_bytes() == b"deep"


@_needs_7z
class TestWrite:
    def test_open_write_appends_member(self, tmp_path):
        archive = _make_7z(tmp_path)
        loc = VfsPath(scheme="7z", root=str(archive), parts=("new.txt",))
        with SevenZipProvider().open_write(loc) as w:
            w.write(b"fresh")
        p = SevenZipProvider()
        names = {e.name for e in p.scan(_root(archive), include_parent=False)}
        assert "new.txt" in names
        with p.open_read(loc) as fh:
            assert fh.read() == b"fresh"

    def test_open_write_compresses_member(self, tmp_path):
        # -mx=9 max compression: highly compressible data must shrink a lot.
        SevenZipProvider().resolve_target("c.7z", base=VfsPath.local(tmp_path))
        archive = tmp_path / "c.7z"
        member = VfsPath(scheme="7z", root=str(archive), parts=("big.txt",))
        with SevenZipProvider().open_write(member) as w:
            w.write(b"A" * 1_000_000)
        assert archive.stat().st_size < 50_000

    def test_attach_progress_reports_full_size_during_compression(self, tmp_path):
        # The writer self-reports 7z's real progress (so the bar doesn't race to
        # 100% then freeze). attach_progress -> True, and the deltas it pushes
        # sum to exactly the bytes fed once the member finishes.
        SevenZipProvider().resolve_target("p.7z", base=VfsPath.local(tmp_path))
        member = VfsPath(
            scheme="7z", root=str(tmp_path / "p.7z"), parts=("big.bin",)
        )
        reported = [0]
        w = SevenZipProvider().open_write(member)
        assert w.attach_progress(lambda n: reported.__setitem__(0, reported[0] + n))
        data = bytes((i * 73) & 0xFF for i in range(2_000_000))  # not trivially RLE
        w.write(data)
        w.close()
        assert reported[0] == len(data)

    def test_open_write_refuses_existing_member(self, tmp_path):
        archive = _make_7z(tmp_path)  # already has a.txt
        loc = VfsPath(scheme="7z", root=str(archive), parts=("a.txt",))
        with pytest.raises(OSError):
            SevenZipProvider().open_write(loc)

    def test_delete_removes_member(self, tmp_path):
        archive = _make_7z(tmp_path)  # a.txt + dir/sub/deep.txt
        p = SevenZipProvider()
        res = p.delete([VfsPath(scheme="7z", root=str(archive), parts=("a.txt",))])
        assert not res.errors
        names = {e.name for e in p.scan(_root(archive), include_parent=False)}
        assert "a.txt" not in names
        assert "dir" in names  # subtree intact

    def test_delete_directory_removes_subtree(self, tmp_path):
        archive = _make_7z(tmp_path)
        p = SevenZipProvider()
        res = p.delete([VfsPath(scheme="7z", root=str(archive), parts=("dir",))])
        assert not res.errors
        names = {e.name for e in p.scan(_root(archive), include_parent=False)}
        assert "dir" not in names
        assert "a.txt" in names

    def test_resolve_target_creates_empty_archive(self, tmp_path):
        loc = SevenZipProvider().resolve_target("brand-new.7z", base=VfsPath.local(tmp_path))
        assert loc is not None
        archive = tmp_path / "brand-new.7z"
        assert archive.is_file()
        # empty but browsable
        names = [e.name for e in SevenZipProvider().scan(loc, include_parent=True)]
        assert names == [".."]

    def test_copy_local_file_into_7z(self, tmp_path):
        archive = _make_7z(tmp_path)
        src = tmp_path / "added.txt"
        src.write_text("payload")
        res = transfer(default_registry(), [VfsPath.local(src)], _root(archive), mode="copy")
        assert not res.errors
        names = {e.name for e in SevenZipProvider().scan(_root(archive), include_parent=False)}
        assert "added.txt" in names


@_needs_7z
class TestPanelEntersSevenZip:
    def test_enter_7z_lists_contents(self, tmp_path):
        from dunders.fm.file_panel import FilePanel

        _make_7z(tmp_path)  # creates tmp_path/test.7z
        panel = FilePanel(cwd=tmp_path)
        panel.refresh_listing()
        panel.cursor = next(i for i, e in enumerate(panel.entries) if e.name == "test.7z")
        panel.activate()
        assert panel.cwd_loc.scheme == "7z"
        assert panel.cwd_loc.parts == ()
        names = {e.name for e in panel.entries if not e.is_parent}
        assert names == {"a.txt", "dir"}

    def test_exit_7z_back_to_local(self, tmp_path):
        from dunders.fm.file_panel import FilePanel

        _make_7z(tmp_path)
        panel = FilePanel(cwd=tmp_path)
        panel.refresh_listing()
        panel.cursor = next(i for i, e in enumerate(panel.entries) if e.name == "test.7z")
        panel.activate()
        panel.ascend()
        assert panel.cwd_loc == VfsPath.local(tmp_path)
        assert panel.entries[panel.cursor].name == "test.7z"  # cursor on archive
