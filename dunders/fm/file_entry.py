"""FileEntry dataclass + display helpers.

Phase 2 owns this module. Phase 3+ (file ops) and Phase 4 (editor/viewer)
read FileEntry instances but never construct them directly — that's the
job of dunders.fm.scan.

VFS migration
-------------
A row is now addressed by a :class:`~dunders.core.vfs.VfsPath` (``loc``)
rather than a bare :class:`pathlib.Path`, so a panel can list an archive,
an SFTP tree, or an API result the same way it lists a local directory.

Backward compatibility is total: the constructor still accepts ``path=`` (a
local ``Path``, auto-wrapped into a ``file``-scheme locator), and ``.path``
remains a read property returning a real ``Path`` for ``file`` entries — so
every existing constructor, selection set, and comparison keeps working.
Reading ``.path`` on a non-``file`` entry raises (that code path is reached
only once non-local providers exist).

``extra`` carries provider-specific columns (git status, docker tag, a JSON
record's fields) so the universal panel can render them without FileEntry
growing a field per provider.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dunders.core.vfs import VfsPath


__all__ = ["FileEntry", "format_size", "format_mtime", "format_mtime_short"]


@dataclass(frozen=True, init=False)
class FileEntry:
    """A row in a file panel listing.

    `name == ".."` marks the synthetic parent-directory entry; `is_parent`
    is the canonical way to check for it (see `dunders.fm.sort` which keeps it
    pinned at the top regardless of sort order).

    Construct with either ``loc=`` (a ``VfsPath``) or the legacy ``path=`` (a
    local ``Path``). ``extra`` is excluded from equality/hash — it is derived
    metadata, not identity.
    """

    loc: VfsPath
    name: str
    size: int
    mtime: float
    is_dir: bool
    is_symlink: bool = False
    is_executable: bool = False
    mode: int = 0          # raw st_mode (from lstat); 0 for synthetic/unknown
    extra: Mapping[str, str] = field(default_factory=dict, compare=False)

    def __init__(
        self,
        *,
        name: str,
        size: int,
        mtime: float,
        is_dir: bool,
        loc: VfsPath | None = None,
        path: Path | str | None = None,
        is_symlink: bool = False,
        is_executable: bool = False,
        mode: int = 0,
        extra: Mapping[str, str] | None = None,
    ) -> None:
        if loc is None:
            if path is None:
                raise TypeError("FileEntry requires either loc= or path=")
            loc = VfsPath.local(path)
        set_ = object.__setattr__  # frozen dataclass: bypass __setattr__ guard
        set_(self, "loc", loc)
        set_(self, "name", name)
        set_(self, "size", size)
        set_(self, "mtime", mtime)
        set_(self, "is_dir", is_dir)
        set_(self, "is_symlink", is_symlink)
        set_(self, "is_executable", is_executable)
        set_(self, "mode", mode)
        set_(self, "extra", dict(extra) if extra else {})

    @property
    def path(self) -> Path:
        """Local filesystem path. Raises for non-``file`` schemes."""
        return self.loc.to_local()

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


def format_mtime_short(mtime: float) -> str:
    """Compact local-time string, fixed 11 chars: 'MM-DD HH:MM'.

    Used by the Detailed view mode where the full 16-char date does not fit
    alongside the attributes column in a half-screen panel.
    """
    return time.strftime("%m-%d %H:%M", time.localtime(mtime))
