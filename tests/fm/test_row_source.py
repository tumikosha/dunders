"""MaterializedRowSource must be a faithful, list-like Sequence so the panel's
indexed/iterated access keeps working unchanged after the RowSource seam.
"""

from collections.abc import Sequence
from pathlib import Path

from dunders.fm.file_entry import FileEntry
from dunders.fm.row_source import MaterializedRowSource, RowSource


def _entry(name: str) -> FileEntry:
    return FileEntry(path=Path("/x") / name, name=name, size=0, mtime=0.0, is_dir=False)


def _src(*names: str) -> MaterializedRowSource:
    return MaterializedRowSource([_entry(n) for n in names])


class TestSequenceContract:
    def test_is_rowsource_and_sequence(self):
        s = _src("a")
        assert isinstance(s, RowSource)
        assert isinstance(s, Sequence)

    def test_len(self):
        assert len(_src("a", "b", "c")) == 3
        assert len(MaterializedRowSource()) == 0

    def test_index_access(self):
        s = _src("a", "b")
        assert s[0].name == "a"
        assert s[1].name == "b"

    def test_negative_index(self):
        s = _src("a", "b")
        assert s[-1].name == "b"

    def test_iteration(self):
        s = _src("a", "b", "c")
        assert [e.name for e in s] == ["a", "b", "c"]

    def test_enumerate(self):
        s = _src("a", "b")
        assert [(i, e.name) for i, e in enumerate(s)] == [(0, "a"), (1, "b")]

    def test_slice_returns_list(self):
        s = _src("a", "b", "c")
        chunk = s[1:]
        assert [e.name for e in chunk] == ["b", "c"]

    def test_out_of_range_raises(self):
        s = _src("a")
        try:
            _ = s[5]
        except IndexError:
            pass
        else:
            raise AssertionError("expected IndexError past the end")

    def test_set_comprehension_over_paths(self):
        s = _src("a", "b")
        paths = {e.path for e in s}
        assert Path("/x/a") in paths and Path("/x/b") in paths
