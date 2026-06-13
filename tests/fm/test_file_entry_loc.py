"""FileEntry's VFS migration: loc-primary storage, path shim, extra columns.

The legacy ``path=`` constructor and ``.path`` reader must keep working
(covered implicitly across the suite); here we pin the new surface directly.
"""

from pathlib import Path

import pytest

from dunders.core.vfs import VfsPath
from dunders.fm.file_entry import FileEntry


def test_path_kwarg_populates_loc():
    e = FileEntry(path=Path("/tmp/x"), name="x", size=1, mtime=0.0, is_dir=False)
    assert e.loc == VfsPath.local(Path("/tmp/x"))
    assert e.path == Path("/tmp/x")


def test_loc_kwarg_construction():
    loc = VfsPath(scheme="zip", root="/a.zip", parts=("inner", "f.txt"))
    e = FileEntry(loc=loc, name="f.txt", size=3, mtime=0.0, is_dir=False)
    assert e.loc == loc


def test_path_on_non_file_scheme_raises():
    loc = VfsPath(scheme="zip", root="/a.zip", parts=("f.txt",))
    e = FileEntry(loc=loc, name="f.txt", size=0, mtime=0.0, is_dir=False)
    with pytest.raises(ValueError):
        _ = e.path


def test_requires_loc_or_path():
    with pytest.raises(TypeError):
        FileEntry(name="x", size=0, mtime=0.0, is_dir=False)


def test_extra_defaults_empty_and_carries_metadata():
    e = FileEntry(path=Path("/tmp/x"), name="x", size=0, mtime=0.0, is_dir=False)
    assert e.extra == {}
    e2 = FileEntry(
        path=Path("/tmp/y"), name="y", size=0, mtime=0.0, is_dir=False,
        extra={"git": "M"},
    )
    assert e2.extra["git"] == "M"


def test_extra_excluded_from_equality():
    base = dict(path=Path("/tmp/x"), name="x", size=0, mtime=0.0, is_dir=False)
    a = FileEntry(**base)
    b = FileEntry(**base, extra={"git": "M"})
    assert a == b  # extra is derived metadata, not identity


def test_still_hashable():
    e = FileEntry(path=Path("/tmp/x"), name="x", size=0, mtime=0.0, is_dir=False)
    assert len({e, e}) == 1
