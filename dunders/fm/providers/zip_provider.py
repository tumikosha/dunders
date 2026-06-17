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
import os
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from dunders.core.vfs import VfsPath
from dunders.core.vfs.provider import ProgressCallback
from dunders.fm.actions import OpError, OpResult
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


class _ZipMemberWriter(io.BytesIO):
    """A write buffer that appends its contents to a zip member on close.

    The generic transfer engine does ``with open_write(dest) as w: copy into w``;
    this buffers the bytes and flushes them via ``writestr`` exactly once when
    the ``with`` block exits.
    """

    def __init__(self, archive: str, inner: str, *, overwrite: bool = False) -> None:
        super().__init__()
        self._archive = archive
        self._inner = inner
        self._overwrite = overwrite
        self._flushed = False

    def close(self) -> None:
        if not self._flushed and not self.closed:
            self._flushed = True
            data = self.getvalue()
            if self._overwrite:
                _replace_member(self._archive, self._inner, data)
            else:
                with zipfile.ZipFile(self._archive, "a") as zf:
                    zf.writestr(self._inner, data)
        super().close()


def _replace_member(archive: str, inner: str, data: bytes) -> None:
    """Rewrite ``archive`` with ``inner`` replaced (zipfile can't edit in place).

    Copies every other member into a sibling temp archive, adds the new bytes,
    then atomically swaps it in.
    """
    tmp = archive + ".tmp"
    with zipfile.ZipFile(archive, "r") as src, \
         zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            if item.filename == inner:
                continue
            dst.writestr(item, src.read(item.filename))
        dst.writestr(inner, data)
    os.replace(tmp, archive)


class ZipProvider:
    """Read/append ``VfsProvider`` for zip archives (structural conformance)."""

    scheme = "zip"
    display_name = "Zip archiver"
    capabilities = frozenset({"read", "stream", "write"})

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

    # -- prefix target (zip:<name> creates a new archive) -----------------

    def resolve_target(
        self, spec: str, *, base: VfsPath, password: str | None = None
    ) -> VfsPath | None:
        """``zip:<name>`` → a locator for a new ``.zip`` under ``base``.

        ``base`` must be a local directory (you cannot create an archive inside
        another archive). The file is created lazily by the first member write.
        """
        if base.scheme != "file":
            return None
        name = (spec or "").strip() or "archive.zip"
        if not name.lower().endswith(".zip"):
            name += ".zip"
        path = Path(name).expanduser()
        if not path.is_absolute():
            path = base.to_local() / path
        # Create-or-open: an absent archive is materialised empty so the panel
        # can browse it immediately (the "_" menu opens it with no copy).
        if not path.exists():
            try:
                with zipfile.ZipFile(path, "w"):
                    pass
            except OSError:
                return None
        return VfsPath(scheme="zip", root=str(path), parts=())

    # -- write (append-only) ----------------------------------------------

    def open_write(
        self, loc: VfsPath, *, size_hint: int | None = None, overwrite: bool = False
    ) -> BinaryIO:
        """Return a writer for ``loc`` flushed to the archive on close.

        Default is append-only: an existing member name is refused. With
        ``overwrite=True`` (editing a member in place) the writer rewrites the
        archive with that member replaced. Bytes are buffered in memory — fine
        for the file sizes a panel/editor deals with.
        """
        inner = "/".join(loc.parts)
        if not inner:
            raise OSError("cannot write the archive root as a member")
        if not overwrite:
            with zipfile.ZipFile(loc.root, "a") as zf:
                if inner in zf.namelist():
                    raise FileExistsError(f"{inner} already exists in archive")
        return _ZipMemberWriter(loc.root, inner, overwrite=overwrite)

    def mkdir(self, parent: VfsPath, name: str) -> OpResult:
        """Add an explicit ``dir/`` entry, preserving empty directories.

        Tolerates a pre-existing directory (no error) so the transfer engine
        can create the same parent twice without failing.
        """
        result = OpResult()
        inner = "/".join((*parent.parts, name))
        if not inner:
            return result
        dirent = inner + "/"
        with zipfile.ZipFile(parent.root, "a") as zf:
            existing = set(zf.namelist())
            if dirent not in existing and not any(
                n.startswith(dirent) for n in existing
            ):
                zf.writestr(dirent, b"")
        # No explicit cache flush needed: _index_for keys on (mtime, size),
        # and appending a member changes the archive's size on disk.
        return result

    def delete(
        self,
        targets: list[VfsPath],
        *,
        on_progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> OpResult:
        """Remove members by rewriting the archive without them.

        A directory target drops its whole subtree (every member under
        ``path/``). All targets must share one archive (a panel selection does).
        """
        result = OpResult()
        inners = ["/".join(t.parts) for t in targets if t.parts]
        if not inners:
            return result
        prefixes = tuple(i + "/" for i in inners)
        drop = set(inners)
        archive = targets[0].root
        tmp = archive + ".tmp"
        try:
            with zipfile.ZipFile(archive, "r") as src, \
                 zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
                for item in src.infolist():
                    name = item.filename.rstrip("/")
                    if name in drop or item.filename.startswith(prefixes):
                        continue
                    dst.writestr(item, src.read(item.filename))
            os.replace(tmp, archive)
        except OSError as exc:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            result.errors.append(OpError(loc=targets[0], reason=str(exc)))
            return result
        if on_progress is not None:
            on_progress(len(inners), len(inners))
        return result

    def copy_within(
        self,
        sources: list[VfsPath],
        dest: VfsPath,
        *,
        rename_to: str | None = None,
        on_progress: ProgressCallback | None = None,
        on_status=None,
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
