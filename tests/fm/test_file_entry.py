from pathlib import Path

import pytest

from dunders.fm.file_entry import FileEntry, format_mtime, format_size


def test_file_entry_is_frozen_dataclass():
    e = FileEntry(
        path=Path("/tmp/x"),
        name="x",
        size=10,
        mtime=0.0,
        is_dir=False,
        is_symlink=False,
        is_executable=False,
    )
    with pytest.raises(Exception):
        e.name = "y"  # frozen


def test_file_entry_is_parent_property():
    e = FileEntry(
        path=Path("/tmp"),
        name="..",
        size=0,
        mtime=0.0,
        is_dir=True,
        is_symlink=False,
        is_executable=False,
    )
    assert e.is_parent is True


def test_file_entry_is_parent_false_for_normal_dir():
    e = FileEntry(
        path=Path("/tmp/sub"),
        name="sub",
        size=0,
        mtime=0.0,
        is_dir=True,
        is_symlink=False,
        is_executable=False,
    )
    assert e.is_parent is False


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (0, "0"),
        (1, "1"),
        (999, "999"),
        (1024, "1.0K"),
        (1536, "1.5K"),
        (1024 * 1024, "1.0M"),
        (1024 * 1024 * 1024, "1.0G"),
        (1024 * 1024 * 1024 * 1024, "1.0T"),
    ],
)
def test_format_size(size: int, expected: str):
    assert format_size(size) == expected


def test_format_mtime_uniform_format():
    """Always 'YYYY-MM-DD HH:MM' regardless of how recent the timestamp is."""
    # Recent and old timestamps both render with the same shape.
    for ts in (1_700_000_000.0, 1_500_000_000.0):
        out = format_mtime(ts)
        assert len(out) == 16
        assert out[4] == "-" and out[7] == "-"
        assert out[10] == " "
        assert out[13] == ":"
