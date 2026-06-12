from pathlib import Path

import pytest

from dunders.fm.file_entry import FileEntry
from dunders.fm.sort import SortOrder, sort_entries


def _entry(name: str, *, is_dir: bool = False, size: int = 0, mtime: float = 0.0) -> FileEntry:
    return FileEntry(
        path=Path("/x") / name,
        name=name,
        size=size,
        mtime=mtime,
        is_dir=is_dir,
        is_symlink=False,
        is_executable=False,
    )


def _parent() -> FileEntry:
    return FileEntry(
        path=Path("/"),
        name="..",
        size=0,
        mtime=0.0,
        is_dir=True,
        is_symlink=False,
        is_executable=False,
    )


def test_parent_pinned_at_top_regardless_of_order():
    raw = [_entry("zzz.txt"), _parent(), _entry("aaa.txt")]
    for order in SortOrder:
        result = sort_entries(raw, order)
        assert result[0].is_parent, f"order={order}"


def test_dirs_before_files_under_name_sort():
    raw = [_entry("zfile.txt"), _entry("adir", is_dir=True), _entry("afile.txt")]
    result = sort_entries(raw, SortOrder.NAME)
    names = [e.name for e in result]
    assert names == ["adir", "afile.txt", "zfile.txt"]


def test_dirs_always_sorted_by_name_even_under_size_order():
    raw = [
        _entry("big_dir", is_dir=True, size=100),
        _entry("small_dir", is_dir=True, size=10),
        _entry("file.txt", size=50),
    ]
    result = sort_entries(raw, SortOrder.SIZE)
    names = [e.name for e in result]
    # dirs first, alphabetical:
    assert names[:2] == ["big_dir", "small_dir"]


def test_size_order_files_ascending():
    raw = [_entry("big", size=1000), _entry("small", size=10), _entry("mid", size=500)]
    result = sort_entries(raw, SortOrder.SIZE)
    sizes = [e.size for e in result]
    assert sizes == [10, 500, 1000]


def test_mtime_order_newest_first():
    raw = [_entry("old", mtime=100.0), _entry("new", mtime=300.0), _entry("mid", mtime=200.0)]
    result = sort_entries(raw, SortOrder.MTIME)
    assert [e.name for e in result] == ["new", "mid", "old"]


def test_ext_order_groups_by_extension_then_name():
    raw = [
        _entry("zfile.py"),
        _entry("afile.py"),
        _entry("file.md"),
        _entry("README"),
    ]
    result = sort_entries(raw, SortOrder.EXT)
    # No-extension files sort first (empty suffix), then .md, then .py.
    assert [e.name for e in result] == ["README", "file.md", "afile.py", "zfile.py"]


def test_name_order_is_case_insensitive():
    raw = [_entry("BBB.txt"), _entry("aaa.txt"), _entry("CCC.txt")]
    result = sort_entries(raw, SortOrder.NAME)
    assert [e.name for e in result] == ["aaa.txt", "BBB.txt", "CCC.txt"]


def test_sort_does_not_mutate_input():
    raw = [_entry("b"), _entry("a")]
    snapshot = list(raw)
    sort_entries(raw, SortOrder.NAME)
    assert raw == snapshot
