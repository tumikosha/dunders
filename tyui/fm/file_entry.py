"""FileEntry dataclass + display helpers.

Phase 2 owns this module. Phase 3+ (file ops) and Phase 4 (editor/viewer)
read FileEntry instances but never construct them directly — that's the
job of tyui.fm.scan.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path


__all__ = ["FileEntry", "format_size", "format_mtime"]


@dataclass(frozen=True)
class FileEntry:
    """A row in a file panel listing.

    `name == ".."` marks the synthetic parent-directory entry; `is_parent`
    is the canonical way to check for it (see `tyui.fm.sort` which keeps it
    pinned at the top regardless of sort order).
    """

    path: Path
    name: str
    size: int
    mtime: float
    is_dir: bool
    is_symlink: bool
    is_executable: bool

    @property
    def is_parent(self) -> bool:
        return self.name == ".."


def format_size(size: int) -> str:
    """Human-readable size: 999 → '999', 1024 → '1.0K', 1.5*1024 → '1.5K'.

    Returns at most 5 characters wide. Used by FilePanel rendering.
    """
    if size < 1024:
        return str(size)
    units = ("K", "M", "G", "T", "P")
    value = float(size) / 1024.0
    for unit in units:
        if value < 1024.0:
            return f"{value:.1f}{unit}"
        value /= 1024.0
    return f"{value:.1f}P"


def format_mtime(mtime: float) -> str:
    """Local-time display string, fixed at 16 characters wide.

    Always 'YYYY-MM-DD HH:MM' regardless of how recent the timestamp is —
    a uniform format keeps the Date column visually aligned.
    """
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
