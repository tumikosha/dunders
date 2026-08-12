"""GDriveProvider — the ``gdrive`` VfsProvider over :class:`DriveApi`.

Drive is an object graph (files have ids, folders may share names, a file can
have several parents), not a path tree. This provider projects it onto the
``VfsPath`` name-path model the panel and the transfer engine expect, resolving
each name to a Drive id per level (cached), the same way the db provider maps
tables → directories. The Drive "root" folder is the special id ``root``.

A ``connector(root) -> DriveApi`` is injected so the OAuth/HTTP machinery stays
out of the path logic — tests pass a connector backed by a fake transport.
"""

from __future__ import annotations

import io
import threading
from collections.abc import Callable
from typing import BinaryIO

from dunders.core.vfs import VfsPath
from dunders.fm.actions import OpError, OpResult
from dunders.fm.file_entry import FileEntry
from dunders.fm.providers.gdrive.api import DriveApi, DriveError, DriveFile


__all__ = ["GDriveProvider"]

_ROOT_ID = "root"


class _DriveWriter(io.BytesIO):
    """Buffers the member, then uploads it to Drive on ``close``."""

    def __init__(self, on_close: Callable[[bytes], None]) -> None:
        super().__init__()
        self._on_close = on_close
        self._done = False

    def close(self) -> None:
        if not self._done:
            self._done = True
            self._on_close(self.getvalue())
        super().close()


class GDriveProvider:
    scheme = "gdrive"
    display_name = "Google Drive"
    capabilities = frozenset({"read", "write", "stream", "slow"})
    # A real remote filesystem (like sftp): the copy dialog offers an editable
    # destination path so a single file can be renamed on the way in.
    remote_fs = True
    # Appears in the "_" dunder menu; the user types an account label.
    display_name = "Google Drive"
    accepts_empty_open = True
    open_placeholder = "Google Drive account label (empty = 'default')"

    def __init__(self, connector: Callable[[str], DriveApi]) -> None:
        self._connect = connector
        self._lock = threading.Lock()
        # (root, parts) -> Drive id cache; cleared per-root on any mutation.
        self._id_cache: dict[tuple[str, tuple[str, ...]], str] = {}

    # --- id resolution -----------------------------------------------------

    def _api(self, root: str) -> DriveApi:
        return self._connect(root)

    def resolve_target(self, spec: str, *, base: VfsPath, password=None) -> VfsPath:
        """Open a Google Drive account by label (from the '_' menu / gdrive: prefix).

        Validates the account is configured by constructing its client — an
        unknown label raises with actionable text — then returns the root loc."""
        label = spec.strip() or "default"
        self._connect(label)  # raises DriveError if the label isn't configured
        return VfsPath(scheme="gdrive", root=label, parts=())

    def _resolve(self, loc: VfsPath) -> str:
        """Drive id for ``loc`` (raises FileNotFoundError if a segment is gone)."""
        key = (loc.root, loc.parts)
        with self._lock:
            cached = self._id_cache.get(key)
        if cached is not None:
            return cached
        api = self._api(loc.root)
        fid = _ROOT_ID
        seen: list[str] = []
        for name in loc.parts:
            child = api.find_child(fid, name)
            if child is None:
                raise FileNotFoundError(f"gdrive: no such item: {'/'.join((*seen, name))}")
            fid = child.id
            seen.append(name)
        with self._lock:
            self._id_cache[key] = fid
        return fid

    def _invalidate(self, root: str) -> None:
        with self._lock:
            self._id_cache = {
                k: v for k, v in self._id_cache.items() if k[0] != root
            }

    # --- reads -------------------------------------------------------------

    def is_dir(self, loc: VfsPath) -> bool:
        if not loc.parts:
            return True  # the account root is a directory
        try:
            return self._api(loc.root).get(self._resolve(loc)).is_dir
        except (DriveError, FileNotFoundError):
            return False

    def scan(
        self, loc: VfsPath, *, show_hidden: bool = False, include_parent: bool = True,
    ) -> list[FileEntry]:
        api = self._api(loc.root)
        folder_id = self._resolve(loc)
        entries: list[FileEntry] = []
        if include_parent and loc.parts:
            entries.append(FileEntry(
                loc=loc.parent, name="..", size=0, mtime=0.0, is_dir=True,
            ))
        for f in api.list_children(folder_id):
            entries.append(self._entry(loc, f))
        return entries

    def _entry(self, parent_loc: VfsPath, f: DriveFile) -> FileEntry:
        return FileEntry(
            loc=parent_loc.child(f.name), name=f.name,
            size=0 if f.is_dir else f.size, mtime=f.mtime, is_dir=f.is_dir,
            extra={"gdrive.id": f.id, "gdrive.mime": f.mime},
        )

    def open_read(self, loc: VfsPath) -> BinaryIO:
        from dunders.fm.providers.gdrive.api import export_format

        api = self._api(loc.root)
        fid = self._resolve(loc)
        meta = api.get(fid)
        if meta.mime.startswith("application/vnd.google-apps"):
            # Native docs (Docs/Sheets/Slides) have no raw content: export them
            # (top-level copies pick the right extension via export_as_file; this
            # path also covers native docs nested inside a copied folder).
            target_mime, _ext = export_format(meta.mime)
            _status, body = api.export(fid, target_mime)
            return body
        _status, body = api.download(fid)
        return body

    def export_as_file(self, loc: VfsPath):
        """Google-native docs (Docs/Sheets/Slides) have no direct content, so
        they're COPIED via the Drive export API to an Office/PDF file with the
        matching extension. Returns ``(name, reader)`` for a native doc (the
        transfer engine's single-file export path uses it) or None for a normal
        file/folder, which copy through open_read as usual."""
        from dunders.fm.providers.gdrive.api import export_format

        if not loc.parts:
            return None
        api = self._api(loc.root)
        try:
            meta = api.get(self._resolve(loc))
        except (DriveError, FileNotFoundError):
            return None
        if meta.is_dir or not meta.mime.startswith("application/vnd.google-apps"):
            return None
        target_mime, ext = export_format(meta.mime)
        _status, body = api.export(meta.id, target_mime)
        name = loc.name if loc.name.endswith(f".{ext}") else f"{loc.name}.{ext}"
        return name, body

    def exists(self, loc: VfsPath) -> bool:
        if not loc.parts:
            return True
        try:
            self._resolve(loc)
            return True
        except (DriveError, FileNotFoundError):
            return False

    # --- writes ------------------------------------------------------------

    def open_write(
        self, loc: VfsPath, *, size_hint: int | None = None, overwrite: bool = False,
    ) -> BinaryIO:
        api = self._api(loc.root)
        parent_id = self._resolve(loc.parent) if loc.parent else _ROOT_ID
        name = loc.name
        existing = api.find_child(parent_id, name)
        if existing is not None and not overwrite:
            raise FileExistsError(f"gdrive: {name} already exists")

        def _upload(data: bytes) -> None:
            # Drive keeps duplicate names, so an overwrite deletes the old item
            # first (simple + correct; the copy engine passes overwrite=True).
            if existing is not None and overwrite:
                api.delete(existing.id)
            api.upload(parent_id, name, data)
            self._invalidate(loc.root)

        return _DriveWriter(_upload)

    def mkdir(self, parent: VfsPath, name: str) -> OpResult:
        result = OpResult()
        api = self._api(parent.root)
        try:
            parent_id = self._resolve(parent)
            if api.find_child(parent_id, name) is None:
                api.create_folder(parent_id, name)
            self._invalidate(parent.root)
        except (DriveError, FileNotFoundError) as exc:
            result.errors.append(OpError(loc=parent.child(name), reason=str(exc)))
        return result

    def delete(self, targets: list[VfsPath], *, on_progress=None,
               cancel_event=None) -> OpResult:
        result = OpResult()
        for i, t in enumerate(targets, 1):
            if cancel_event is not None and cancel_event.is_set():
                result.cancelled = True
                break
            try:
                self._api(t.root).delete(self._resolve(t))
                result.succeeded.append(t)
            except (DriveError, FileNotFoundError) as exc:
                result.errors.append(OpError(loc=t, reason=str(exc)))
            if on_progress is not None:
                on_progress(i, len(targets))
        self._invalidate(targets[0].root) if targets else None
        return result

    # Intra-scheme copy/move has no fast path — the generic engine streams it.
    def copy_within(self, sources, dest, *, rename_to=None, on_progress=None,
                    on_status=None, cancel_event=None, skip_existing=False):
        return None

    def move_within(self, sources, dest, *, rename_to=None, on_progress=None,
                    cancel_event=None):
        return None
