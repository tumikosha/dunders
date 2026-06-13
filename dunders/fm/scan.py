"""scan_dir — build a list of FileEntry rows for a directory.

Errors are swallowed by design: an unreadable child, a vanishing race,
or a permission-denied iterdir all yield best-effort partial output
rather than an exception. The panel uses this directly on every refresh
and must not blow up on missing/locked filesystem state.
"""

from __future__ import annotations

import os
from pathlib import Path

from dunders.fm.file_entry import FileEntry


__all__ = ["scan_dir"]


def scan_dir(
    cwd: Path,
    *,
    show_hidden: bool = False,
    include_parent: bool = True,
) -> list[FileEntry]:
    """Return one FileEntry per child of `cwd` (best-effort).

    Parameters
    ----------
    cwd:
        Directory to read.
    show_hidden:
        If False, names beginning with '.' are filtered out.
    include_parent:
        If True and cwd has a distinct parent (i.e. cwd is not a filesystem
        root), prepend a synthetic ".." entry pointing at the parent.
    """
    entries: list[FileEntry] = []

    if include_parent:
        parent = cwd.parent
        if parent != cwd:
            try:
                pst = parent.stat()
                entries.append(FileEntry(
                    path=parent,
                    name="..",
                    size=0,
                    mtime=pst.st_mtime,
                    is_dir=True,
                    is_symlink=parent.is_symlink(),
                    is_executable=False,
                    mode=pst.st_mode,
                ))
            except OSError:
                # Parent unreadable — no parent row, just skip.
                pass

    # os.scandir reads d_type with the directory entry, so is_symlink/is_dir
    # come for free without a syscall on regular files. Only the metadata
    # (size/mtime/mode) costs one lstat-equivalent per child — down from the
    # ~3 stats per child the old iterdir + lstat + is_dir + is_symlink did.
    try:
        scan = os.scandir(cwd)
    except OSError:
        return entries

    with scan:
        for entry in scan:
            name = entry.name
            if not show_hidden and name.startswith("."):
                continue
            try:
                st = entry.stat(follow_symlinks=False)  # lstat-equivalent, cached
            except OSError:
                # vanished / permission-denied — skip silently
                continue
            try:
                is_symlink = entry.is_symlink()
            except OSError:
                is_symlink = False
            # is_dir() follows symlinks; that's the right behaviour for
            # navigation (Enter on a symlink-to-dir descends into the target).
            try:
                is_dir = entry.is_dir()
            except OSError:
                is_dir = False
            is_executable = (not is_dir) and bool(st.st_mode & 0o111)
            entries.append(FileEntry(
                path=Path(entry.path),
                name=name,
                size=0 if is_dir else st.st_size,
                mtime=st.st_mtime,
                is_dir=is_dir,
                is_symlink=is_symlink,
                is_executable=is_executable,
                mode=st.st_mode,
            ))
    return entries
