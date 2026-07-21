"""_resolve_copy_dest — how a copy/move destination path splits into
(dest_dir, rename_to), and specifically that it never nests a directory
inside a same-named existing directory (dest/front -> dest/front/front)."""

from pathlib import Path

from dunders.app import _resolve_copy_dest


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
