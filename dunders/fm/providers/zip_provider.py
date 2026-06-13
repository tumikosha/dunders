"""ZipProvider — browse a ``.zip`` archive as if it were a directory tree.

The first *foreign* VFS provider: read-only, materialized. It proves the
provider contract against a non-local source.

Addressing
----------
``VfsPath(scheme="zip", root="/abs/path/archive.zip", parts=("dir", "f.txt"))``
— ``root`` is the archive on the local disk, ``parts`` is the path *inside* it.

Index
-----
A zip's ``namelist()`` is flat and may omit directory entries, so on first
access the provider synthesises a directory tree from the member names and
caches it keyed by ``(path, mtime, size)``; the cache invalidates if the
archive changes on disk. Listing a node is then a dict lookup.

Scope
-----
Read-only v1: ``scan`` + ``open_read`` (members are read fully into memory —
fine for F3 viewing). Writes/mkdir/delete raise; ``copy_within``/``move_within``
return ``None`` so extracting *out* of an archive falls to the (not-yet-built)
generic cross-provider transfer rather than silently failing.
"""

from __future__ import annotations

import io
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from dunders.core.vfs import VfsPath
from dunders.core.vfs.provider import ProgressCallback
from dunders.fm.actions import OpResult
from dunders.fm.file_entry import FileEntry


__all__ = ["ZipProvider"]


@dataclass(frozen=True)
class _Node:
    name: str
    is_dir: bool
    size: int
    mtime: float


# parent-parts tuple -> list of immediate child nodes
_Index = dict[tuple[str, ...], list[_Node]]


def _zip_mtime(date_time: tuple[int, int, int, int, int, int]) -> float:
    try:
        return time.mktime((*date_time, 0, 0, -1))
    except (ValueError, OverflowError):
        return 0.0


def _build_index(zf: zipfile.ZipFile) -> _Index:
    # name -> node, grouped by parent path; dict keeps last-wins + dedup.
    grouped: dict[tuple[str, ...], dict[str, _Node]] = {}

    def ensure_dir(parts: tuple[str, ...]) -> None:
        """Register ``parts`` and every ancestor as directory nodes."""
        for i in range(len(parts)):
            parent = parts[:i]
            name = parts[i]
            bucket = grouped.setdefault(parent, {})
            if name not in bucket:
                bucket[name] = _Node(name=name, is_dir=True, size=0, mtime=0.0)

    for info in zf.infolist():
        parts = tuple(p for p in info.filename.split("/") if p)
        if not parts:
            continue
        if info.filename.endswith("/"):
            ensure_dir(parts)
            continue
        ensure_dir(parts[:-1])
        bucket = grouped.setdefault(parts[:-1], {})
        bucket[parts[-1]] = _Node(
            name=parts[-1],
            is_dir=False,
            size=info.file_size,
            mtime=_zip_mtime(info.date_time),
        )

    return {parent: list(nodes.values()) for parent, nodes in grouped.items()}


class ZipProvider:
    """Read-only ``VfsProvider`` for zip archives (structural conformance)."""

    scheme = "zip"
    capabilities = frozenset({"read", "stream"})

    def __init__(self) -> None:
        # archive path -> ((mtime, size), index)
        self._cache: dict[str, tuple[tuple[float, int], _Index]] = {}

    # -- index cache ------------------------------------------------------

    def _index_for(self, loc: VfsPath) -> _Index:
        path = loc.root
        st = Path(path).stat()
        sig = (st.st_mtime, st.st_size)
        cached = self._cache.get(path)
        if cached is not None and cached[0] == sig:
            return cached[1]
        with zipfile.ZipFile(path) as zf:
            index = _build_index(zf)
        self._cache[path] = (sig, index)
        return index

    # -- VfsProvider ------------------------------------------------------

    def scan(
        self,
        loc: VfsPath,
        *,
        show_hidden: bool = False,
        include_parent: bool = True,
    ) -> list[FileEntry]:
        index = self._index_for(loc)
        entries: list[FileEntry] = []
        if include_parent:
            entries.append(self._parent_entry(loc))
        for node in index.get(loc.parts, []):
            if not show_hidden and node.name.startswith("."):
                continue
            entries.append(FileEntry(
                loc=loc.child(node.name),
                name=node.name,
                size=node.size,
                mtime=node.mtime,
                is_dir=node.is_dir,
            ))
        return entries

    def _parent_entry(self, loc: VfsPath) -> FileEntry:
        """The '..' row. Inside the archive it goes up a level; at the archive
        root it exits to the local directory that contains the .zip."""
        parent = loc.parent
        if parent is None:
            parent = VfsPath.local(Path(loc.root).parent)
        return FileEntry(loc=parent, name="..", size=0, mtime=0.0, is_dir=True)

    def is_dir(self, loc: VfsPath) -> bool:
        if not loc.parts:
            return True  # archive root
        index = self._index_for(loc)
        if loc.parts in index:
            return True  # has children -> directory
        parent = loc.parts[:-1]
        name = loc.parts[-1]
        return any(n.name == name and n.is_dir for n in index.get(parent, []))

    def open_read(self, loc: VfsPath) -> BinaryIO:
        inner = "/".join(loc.parts)
        with zipfile.ZipFile(loc.root) as zf:
            data = zf.read(inner)
        return io.BytesIO(data)

    # -- read-only: mutations are unsupported -----------------------------

    def open_write(self, loc: VfsPath, *, size_hint: int | None = None) -> BinaryIO:
        raise OSError("zip archives are read-only")

    def mkdir(self, parent: VfsPath, name: str) -> OpResult:
        raise OSError("zip archives are read-only")

    def delete(
        self,
        targets: list[VfsPath],
        *,
        on_progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> OpResult:
        raise OSError("zip archives are read-only")

    def copy_within(
        self,
        sources: list[VfsPath],
        dest: VfsPath,
        *,
        rename_to: str | None = None,
        on_progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> OpResult | None:
        return None  # no intra-zip fast path; extraction is cross-provider

    def move_within(
        self,
        sources: list[VfsPath],
        dest: VfsPath,
        *,
        rename_to: str | None = None,
        on_progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> OpResult | None:
        return None
