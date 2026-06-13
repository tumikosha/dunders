"""FilePanel can enter a .zip and browse it like a directory tree.

Exercises the Path->VfsPath generalization end to end: entering an archive,
descending/ascending inside it, exiting back to the local filesystem, and
selection behaviour for non-local entries.
"""

import zipfile
from pathlib import Path

from dunders.core.vfs import VfsPath
from dunders.fm.file_panel import FilePanel


def _make_archive(tmp_path: Path) -> Path:
    path = tmp_path / "a.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("top.txt", b"hello")
        zf.writestr("dir/inner.txt", b"world")
    return path


def _cursor_on(p: FilePanel, name: str) -> None:
    p.cursor = next(i for i, e in enumerate(p.entries) if e.name == name)


def _panel_at_zip_root(tmp_path: Path) -> FilePanel:
    _make_archive(tmp_path)
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    _cursor_on(p, "a.zip")
    p.activate()
    return p


def test_enter_zip_lists_contents(tmp_path: Path):
    p = _panel_at_zip_root(tmp_path)
    assert p.cwd_loc.scheme == "zip"
    assert p.cwd_loc.parts == ()
    names = {e.name for e in p.entries}
    assert "top.txt" in names and "dir" in names


def test_descend_into_dir_inside_zip(tmp_path: Path):
    p = _panel_at_zip_root(tmp_path)
    _cursor_on(p, "dir")
    p.activate()
    assert p.cwd_loc.scheme == "zip"
    assert p.cwd_loc.parts == ("dir",)
    assert {e.name for e in p.entries if not e.is_parent} == {"inner.txt"}


def test_ascend_within_zip_returns_to_root(tmp_path: Path):
    p = _panel_at_zip_root(tmp_path)
    _cursor_on(p, "dir")
    p.activate()
    p.ascend()
    assert p.cwd_loc.scheme == "zip"
    assert p.cwd_loc.parts == ()


def test_ascend_at_zip_root_exits_to_local_dir(tmp_path: Path):
    p = _panel_at_zip_root(tmp_path)
    p.ascend()
    assert p.cwd_loc == VfsPath.local(tmp_path)
    assert any(e.name == "a.zip" for e in p.entries)


def test_parent_row_inside_zip_exits_via_activate(tmp_path: Path):
    p = _panel_at_zip_root(tmp_path)
    # The ".." row at the archive root points back to the local folder.
    assert p.entries[0].is_parent
    _cursor_on(p, "..")
    p.activate()
    assert p.cwd_loc == VfsPath.local(tmp_path)


def test_selection_inside_zip_excluded_from_paths(tmp_path: Path):
    p = _panel_at_zip_root(tmp_path)
    _cursor_on(p, "top.txt")
    p.toggle_selection()
    assert p.selection  # a zip-scheme VfsPath got selected
    # ...but selected_paths() yields nothing: extraction isn't wired yet.
    assert p.selected_paths() == []


def test_cwd_degrades_to_archive_path_inside_zip(tmp_path: Path):
    p = _panel_at_zip_root(tmp_path)
    # No real local cwd inside an archive; degrade to the .zip's own path so
    # pathlib-speaking host code doesn't crash.
    assert p.cwd == tmp_path / "a.zip"
