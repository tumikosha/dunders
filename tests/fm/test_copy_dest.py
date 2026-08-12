"""_resolve_copy_dest — how a copy/move destination path splits into
(dest_dir, rename_to), and specifically that it never nests a directory
inside a same-named existing directory (dest/front -> dest/front/front)."""

from pathlib import Path

from dunders.app import _parse_remote_copy_dest, _resolve_copy_dest
from dunders.core.vfs import VfsPath


def test_new_target_uses_parent_and_rename(tmp_path: Path):
    # dest/front does not exist yet: copy `front` as dest/front.
    dest = tmp_path / "dest"
    dest.mkdir()
    dest_dir, rename_to = _resolve_copy_dest(dest / "front", "front")
    assert dest_dir == dest
    assert rename_to == "front"


def test_existing_same_name_target_does_not_nest(tmp_path: Path):
    # dest/front ALREADY exists (re-copy / update). Must merge into it, not
    # produce dest/front/front — the reported sftp bug.
    dest = tmp_path / "dest"
    (dest / "front").mkdir(parents=True)
    dest_dir, rename_to = _resolve_copy_dest(dest / "front", "front")
    assert dest_dir == dest
    assert rename_to == "front"
    # Engine appends rename_to -> dest/front, not dest/front/front.
    assert dest_dir / rename_to == dest / "front"


def test_existing_different_dir_is_a_container(tmp_path: Path):
    # An existing directory whose name differs from the source is a container:
    # copy `front` INTO it -> other/front.
    other = tmp_path / "other"
    other.mkdir()
    dest_dir, rename_to = _resolve_copy_dest(other, "front")
    assert dest_dir == other
    assert rename_to is None


def test_multi_item_drops_into_container(tmp_path: Path):
    # No single source name -> always a container, never a rename.
    dest = tmp_path / "dest"
    dest.mkdir()
    dest_dir, rename_to = _resolve_copy_dest(dest, None)
    assert dest_dir == dest
    assert rename_to is None


# --- _parse_remote_copy_dest (sftp/ftp rename on copy) ----------------------


def _sftp(*parts):
    return VfsPath(scheme="sftp", root="bob@h:22", parts=parts)


def test_remote_single_edited_name_becomes_rename():
    # Editing the prefilled locator's basename renames the file on the way in.
    d, rename = _parse_remote_copy_dest(_sftp("d"), "sftp://bob@h:22!/d/new.txt",
                                        single=True)
    assert d == _sftp("d")
    assert rename == "new.txt"


def test_remote_single_into_root():
    d, rename = _parse_remote_copy_dest(_sftp(), "sftp://bob@h:22!/new.txt",
                                        single=True)
    assert d == _sftp()
    assert rename == "new.txt"


def test_remote_multi_no_rename():
    d, rename = _parse_remote_copy_dest(_sftp("d"), "sftp://bob@h:22!/d",
                                        single=False)
    assert d == _sftp("d")
    assert rename is None


def test_remote_empty_or_bad_raw_uses_dest_dir():
    assert _parse_remote_copy_dest(_sftp("d"), "", single=True) == (_sftp("d"), None)
    assert _parse_remote_copy_dest(_sftp("d"), "!!not a uri", single=True) == (_sftp("d"), None)
