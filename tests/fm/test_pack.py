"""pack_paths — pack a local selection into a new .zip."""

import threading
import zipfile
from pathlib import Path

from dunders.fm.actions import pack_paths


def _tree(tmp_path: Path) -> Path:
    base = tmp_path / "src"
    base.mkdir()
    (base / "a.txt").write_text("aaa")
    (base / "dir").mkdir()
    (base / "dir" / "inner.txt").write_text("inner")
    (base / "dir" / "sub").mkdir()
    (base / "dir" / "sub" / "deep.txt").write_text("deep")
    return base


def _names(zip_path: Path) -> set[str]:
    with zipfile.ZipFile(zip_path) as zf:
        return set(zf.namelist())


class TestPack:
    def test_pack_single_file(self, tmp_path):
        base = _tree(tmp_path)
        dest = tmp_path / "out.zip"
        res = pack_paths([base / "a.txt"], dest, base=base)
        assert not res.errors
        assert res.succeeded == [dest]
        assert _names(dest) == {"a.txt"}
        with zipfile.ZipFile(dest) as zf:
            assert zf.read("a.txt") == b"aaa"

    def test_pack_directory_recursively_with_arcnames(self, tmp_path):
        base = _tree(tmp_path)
        dest = tmp_path / "out.zip"
        res = pack_paths([base / "dir"], dest, base=base)
        assert not res.errors
        # arcnames are relative to base (the panel cwd), not absolute.
        assert _names(dest) == {"dir/inner.txt", "dir/sub/deep.txt"}

    def test_pack_mixed_selection(self, tmp_path):
        base = _tree(tmp_path)
        dest = tmp_path / "out.zip"
        pack_paths([base / "a.txt", base / "dir"], dest, base=base)
        assert _names(dest) == {"a.txt", "dir/inner.txt", "dir/sub/deep.txt"}

    def test_empty_dir_preserved(self, tmp_path):
        base = tmp_path / "src"
        base.mkdir()
        (base / "empty").mkdir()
        dest = tmp_path / "out.zip"
        pack_paths([base / "empty"], dest, base=base)
        assert "empty/" in _names(dest)

    def test_round_trips_content(self, tmp_path):
        base = _tree(tmp_path)
        dest = tmp_path / "out.zip"
        pack_paths([base / "dir"], dest, base=base)
        with zipfile.ZipFile(dest) as zf:
            assert zf.read("dir/sub/deep.txt") == b"deep"


class TestRefusals:
    def test_refuses_existing_dest(self, tmp_path):
        base = _tree(tmp_path)
        dest = tmp_path / "out.zip"
        dest.write_text("dont clobber me")
        res = pack_paths([base / "a.txt"], dest, base=base)
        assert len(res.errors) == 1
        assert "exist" in res.errors[0].reason.lower()
        assert dest.read_text() == "dont clobber me"  # untouched

    def test_missing_source_reported(self, tmp_path):
        base = _tree(tmp_path)
        dest = tmp_path / "out.zip"
        res = pack_paths([base / "ghost.txt"], dest, base=base)
        assert len(res.errors) == 1
        assert res.errors[0].path == base / "ghost.txt"


class TestProgressCancel:
    def test_progress_reported(self, tmp_path):
        base = _tree(tmp_path)
        dest = tmp_path / "out.zip"
        seen: list[tuple[int, int]] = []
        pack_paths([base / "dir"], dest, base=base,
                   on_progress=lambda i, n: seen.append((i, n)))
        assert seen  # at least one progress tick
        assert seen[-1][0] == seen[-1][1]  # ends at 100%

    def test_cancel_removes_partial_archive(self, tmp_path):
        base = _tree(tmp_path)
        dest = tmp_path / "out.zip"
        ev = threading.Event()
        ev.set()  # cancel before any file is written
        res = pack_paths([base / "dir"], dest, base=base, cancel_event=ev)
        assert res.cancelled is True
        assert not dest.exists()  # partial archive cleaned up
