"""SortOrder enum + sort_entries function.

Invariant maintained by sort_entries:
    parent ("..") row first, then directories alphabetical (independent of
    the chosen order), then files in the chosen order. This matches mc and
    keeps cursor positions predictable across sort changes.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from pathlib import PurePath

from dunders.fm.file_entry import FileEntry


__all__ = ["SortOrder", "default_descending", "sort_entries"]


class SortOrder(StrEnum):
    NAME = "name"
    SIZE = "size"
    MTIME = "mtime"
    EXT = "ext"


def _name_key(e: FileEntry) -> str:
    return e.name.lower()


def _size_key(e: FileEntry) -> int:
    return e.size


def _mtime_key(e: FileEntry) -> float:
    return e.mtime


def _ext_key(e: FileEntry) -> tuple[str, str]:
    suffix = PurePath(e.name).suffix.lower()
    return (suffix, e.name.lower())


_FILE_KEYS = {
    SortOrder.NAME: _name_key,
    SortOrder.SIZE: _size_key,
    SortOrder.MTIME: _mtime_key,
    SortOrder.EXT: _ext_key,
}

# Per-order default direction. Picked to match common file-manager UX:
# alphabetical orders ascend (A→Z, smallest extension first, smallest size
# first); date descends so the newest entries appear at the top.
_DEFAULT_DESCENDING = {
    SortOrder.NAME: False,
    SortOrder.EXT: False,
    SortOrder.SIZE: False,
    SortOrder.MTIME: True,
}


def default_descending(order: SortOrder) -> bool:
    return _DEFAULT_DESCENDING[order]


def sort_entries(
    entries: list[FileEntry],
    order: SortOrder,
    *,
    descending: bool | None = None,
    key: Callable[[FileEntry], object] | None = None,
) -> list[FileEntry]:
    """Return a new list with parent first, directories alphabetical,
    files in the chosen order. Input list is not mutated.

    ``descending`` defaults to :func:`default_descending` for the given order.
    Directories always sort ascending by name regardless of ``descending`` —
    keeping them grouped at the top is more useful than mirroring file order.
    """
    if descending is None:
        descending = _DEFAULT_DESCENDING[order]
    parent = [e for e in entries if e.is_parent]
    if key is not None:
        # Custom key (e.g. a provider column like Docker state): sort every
        # non-parent entry by it, bypassing the dirs-before-files grouping —
        # otherwise an all-directory listing (containers) could never reorder.
        rest = sorted(
            (e for e in entries if not e.is_parent),
            key=key,
            reverse=descending,
        )
        return [*parent, *rest]
    dirs = sorted(
        (e for e in entries if e.is_dir and not e.is_parent),
        key=_name_key,
    )
    files = sorted(
        (e for e in entries if not e.is_dir),
        key=_FILE_KEYS[order],
        reverse=descending,
    )
    return [*parent, *dirs, *files]
