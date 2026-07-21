"""scan_dir — build a list of FileEntry rows for a directory.

Per-child errors are swallowed by design: an unreadable child or a vanishing
race yields best-effort partial output rather than an exception. But a failure
to open the *directory itself* (``os.scandir`` raising — e.g. a chmod-000 dir,
or a macOS TCC-protected folder like ~/Downloads when the terminal lacks the
grant) is re-raised: an empty listing there is not a real result, it is a
concealed permission error. ``FilePanel.refresh_listing`` catches it and shows a
"Listing failed: …" toast so the user sees *why* the folder looks empty instead
of an unexplained blank pane.
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
    # A failure to open the directory itself (permission denied, vanished,
    # TCC-blocked on macOS) is NOT best-effort partial output — it is the whole
    # listing failing. Re-raise so the panel can report it instead of rendering
    # a misleading empty pane. Per-child errors below stay swallowed.
    scan = os.scandir(cwd)

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
