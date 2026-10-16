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

Passwords
---------
Encrypted archives (AES via pyzipper, legacy ZipCrypto read-only) are listed
without a password — the central directory is never encrypted — but reading a
member needs one. Passwords live only in ``ZipProvider._passwords`` (memory,
keyed by archive path; never persisted). The panel asks for one before
entering an archive whose ``needs_unlock`` is true. Once known, every write
(append, edit, delete-rewrite, mkdir) encrypts new members with AES-256, so the
archive stays fully protected. An empty or all-whitespace password means "no
password" everywhere (:func:`normalize_password`).

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
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import pyzipper

from dunders.core.vfs import VfsPath
from dunders.core.vfs.provider import ProgressCallback
from dunders.fm.actions import OpError, OpResult
from dunders.fm.file_entry import FileEntry


__all__ = ["ZipPasswordRequired", "ZipProvider", "normalize_password"]


def normalize_password(value: str | None) -> str | None:
    """The one rule for every password field: blank or whitespace-only means
    *no password* (``None``); anything else is used verbatim."""
    if value is None or not value.strip():
        return None
    return value


class ZipPasswordRequired(OSError):
    """A member is encrypted and no password is known for its archive."""


def _open_zip(path: str, mode: str = "r", pwd: bytes | None = None, **kwargs):
    """Open ``path`` with pyzipper; with ``pwd`` set, reads decrypt with it and
    every member written is AES-256 encrypted."""
    zf = pyzipper.AESZipFile(path, mode, **kwargs)
    if pwd is not None:
        zf.setpassword(pwd)
        if mode != "r":
            zf.setencryption(pyzipper.WZ_AES, nbits=256)
    return zf


# check_password reads at most this much of its probe member.
_PROBE_BYTES = 64 * 1024


def _is_encrypted(info: zipfile.ZipInfo) -> bool:
    return bool(info.flag_bits & 0x1)


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


class _ZipMemberWriter(io.RawIOBase):
    """A writable stream that feeds its bytes into a zip member.

    The generic transfer engine does ``with open_write(dest) as w: copy into w``
    and drives a byte progress bar off each ``write``. Two flush strategies:

    * **append (default)** — open the archive and a streaming member writer
      (``ZipFile.open(inner, "w")``) up front, so every ``write`` *deflates and
      writes that chunk to disk immediately*. Progress tracks real compression
      and memory stays bounded (a multi-GB file is never buffered whole). The
      old ``BytesIO`` design buffered everything then compressed in ``close`` —
      the bar hit 100% instantly and froze during the (unaccounted) flush.
    * **overwrite** — zipfile can't rewrite a member in place, so buffer in
      memory and rebuild the archive via :func:`_replace_member` on close. Used
      only by the F4 editor save (small, in-memory bytes), never the big-copy
      path, so it has no progress concern.

    The existing-member dup check (append only) runs in ``__init__`` so a
    ``FileExistsError`` still propagates out of ``open_write``.
    """

    def __init__(
        self,
        archive: str,
        inner: str,
        *,
        overwrite: bool = False,
        pwd: bytes | None = None,
    ) -> None:
        super().__init__()
        self._archive = archive
        self._pwd = pwd
        self._inner = inner
        self._buf: io.BytesIO | None = None
        self._zf: zipfile.ZipFile | None = None
        self._member: BinaryIO | None = None
        self._report = None  # progress sink for the buffered overwrite path
        # A copy passes overwrite=True for every file (it may clobber), but only
        # a member that already exists needs the buffer-and-rebuild path. A new
        # member streams like append: buffering it made the copy bar race to
        # 100% and then freeze while the whole archive was rewritten in close.
        if overwrite and Path(archive).exists():
            with _open_zip(archive, "r", pwd) as zf:
                overwrite = inner in zf.namelist()
        else:
            overwrite = False
        self._overwrite = overwrite
        if overwrite:
            self._buf = io.BytesIO()
            return
        # ZIP_DEFLATED at max level (9): the ZipFile default is ZIP_STORED
        # (uncompressed). force_zip64 because the streamed size is unknown up
        # front, so a >4 GiB member must be allowed. Existing members keep their
        # own compression — this only governs the new one.
        zf = _open_zip(
            archive, "a", pwd, compression=zipfile.ZIP_DEFLATED, compresslevel=9
        )
        try:
            if inner in zf.namelist():
                raise FileExistsError(f"{inner} already exists in archive")
            self._member = zf.open(inner, "w", force_zip64=True)
        except BaseException:
            zf.close()
            raise
        self._zf = zf

    def attach_progress(self, report) -> bool:
        """Buffered overwrite writes nothing until ``close`` rebuilds the
        archive, so own the byte counter and report the member then; the
        streaming path writes per chunk and lets the engine count."""
        if not self._overwrite:
            return False
        self._report = report
        return True

    def writable(self) -> bool:
        return True

    def write(self, b) -> int:
        if self._overwrite:
            return self._buf.write(b)
        return self._member.write(b)

    def close(self) -> None:
        if self.closed:
            return
        try:
            if self._overwrite:
                if self._buf is not None:
                    data = self._buf.getvalue()
                    _replace_member(self._archive, self._inner, data, self._pwd)
                    if self._report is not None and data:
                        self._report(len(data))
            else:
                if self._member is not None:
                    self._member.close()
                if self._zf is not None:
                    self._zf.close()
        finally:
            super().close()


def _replace_member(
    archive: str, inner: str, data: bytes, pwd: bytes | None = None
) -> None:
    """Rewrite ``archive`` with ``inner`` replaced (zipfile can't edit in place).

    Copies every other member into a sibling temp archive, adds the new bytes,
    then atomically swaps it in. With ``pwd`` the copy decrypts with it and
    re-encrypts everything.
    """
    tmp = archive + ".tmp"
    with _open_zip(archive, "r", pwd) as src, \
         _open_zip(tmp, "w", pwd, compression=zipfile.ZIP_DEFLATED,
                   compresslevel=9) as dst:
        for item in src.infolist():
            if item.filename == inner:
                continue
            dst.writestr(item, src.read(item.filename))
        dst.writestr(inner, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    os.replace(tmp, archive)


class ZipProvider:
    """Read/append ``VfsProvider`` for zip archives (structural conformance)."""

    scheme = "zip"
    display_name = "Zip archiver"
    capabilities = frozenset({"read", "stream", "write"})

    # The "_" menu / copy dialog show a password field for new archives.
    supports_password = True

    def __init__(self) -> None:
        # archive path -> ((mtime, size), index, has encrypted members)
        self._cache: dict[str, tuple[tuple[float, int], _Index, bool]] = {}
        # archive path -> password (memory only, never persisted)
        self._passwords: dict[str, bytes] = {}

    # -- index cache ------------------------------------------------------

    def _entry_for(self, path: str) -> tuple[_Index, bool]:
        st = Path(path).stat()
        sig = (st.st_mtime, st.st_size)
        cached = self._cache.get(path)
        if cached is not None and cached[0] == sig:
            return cached[1], cached[2]
        with _open_zip(path) as zf:
            index = _build_index(zf)
            encrypted = any(_is_encrypted(i) for i in zf.infolist())
        self._cache[path] = (sig, index, encrypted)
        return index, encrypted

    def _index_for(self, loc: VfsPath) -> _Index:
        return self._entry_for(loc.root)[0]

    # -- passwords --------------------------------------------------------

    @staticmethod
    def _key(root: str) -> str:
        # One key per real file: "sub/../a.zip", "/tmp/a.zip" vs
        # "/private/tmp/a.zip" must all share the remembered password.
        return os.path.realpath(root)

    def _pwd(self, root: str) -> bytes | None:
        return self._passwords.get(self._key(root))

    def forget_password(self, root: str) -> None:
        """Drop a remembered password (stale: the archive was replaced or the
        password stopped working) so the next entry asks again."""
        self._passwords.pop(self._key(root), None)

    def is_encrypted(self, root: str) -> bool:
        """True when any member of the archive at ``root`` is encrypted."""
        try:
            return self._entry_for(root)[1]
        except (OSError, zipfile.BadZipFile):
            return False

    def needs_unlock(self, loc: VfsPath) -> bool:
        """The archive is encrypted and no password is known for it yet."""
        return self._pwd(loc.root) is None and self.is_encrypted(loc.root)

    def _require_unlocked(self, root: str) -> None:
        """Refuse to write into an encrypted archive without its password —
        the new member would land unencrypted (and a rewrite can't read the
        existing ones)."""
        if self.needs_unlock(VfsPath(scheme="zip", root=root, parts=())):
            raise ZipPasswordRequired(f"{Path(root).name} is password-protected")

    def set_password(self, root: str, password: str) -> None:
        self._passwords[self._key(root)] = password.encode("utf-8")

    def check_password(self, root: str, password: str) -> bool:
        """Try ``password`` on the smallest non-empty encrypted member.

        Reads at most ``_PROBE_BYTES`` so a huge member never freezes the UI.
        A real read (not just the header check) matters: ZipCrypto's one-byte
        check passes ~1 in 256 wrong passwords, which then fail on inflate —
        and an *empty* member can't catch that, hence ``file_size > 0`` first.
        ``OSError``/``NotImplementedError`` (archive gone, unsupported
        compression) propagate: they are not a wrong password.
        """
        pwd = password.encode("utf-8")
        with _open_zip(root) as zf:
            enc = [i for i in zf.infolist() if _is_encrypted(i)]
            if not enc:
                return True
            files = [i for i in enc if not i.is_dir() and i.file_size > 0] or enc
            probe = min(files, key=lambda i: i.compress_size)
            try:
                with zf.open(probe, pwd=pwd) as member:
                    member.read(_PROBE_BYTES)
            except (RuntimeError, zlib.error, zipfile.BadZipFile, ValueError):
                return False
        return True

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
        pwd = self._pwd(loc.root)
        with _open_zip(loc.root, pwd=pwd) as zf:
            if pwd is None and _is_encrypted(zf.getinfo(inner)):
                raise ZipPasswordRequired(
                    f"{Path(loc.root).name} is password-protected"
                )
            try:
                data = zf.read(inner)
            except (RuntimeError, zlib.error, zipfile.BadZipFile) as exc:
                # Bad/stale password (pyzipper RuntimeError, or a ZipCrypto
                # false positive failing on inflate/CRC): forget it so the
                # next entry into the archive asks again.
                if pwd is not None:
                    self.forget_password(loc.root)
                raise ZipPasswordRequired(str(exc)) from exc
            except NotImplementedError as exc:  # e.g. Deflate64/PPMd
                raise OSError(str(exc)) from exc
        return io.BytesIO(data)

    # -- prefix target (zip:<name> creates a new archive) -----------------

    def resolve_target(
        self, spec: str, *, base: VfsPath, password: str | None = None
    ) -> VfsPath | None:
        """``zip:<name>`` → a locator for a new ``.zip`` under ``base``.

        ``base`` must be a local directory (you cannot create an archive inside
        another archive). The file is created lazily by the first member write.

        ``password`` (blank = none, see :func:`normalize_password`) protects a
        new archive — every member written into it is AES-encrypted — or
        unlocks an existing encrypted one (``PermissionError`` when wrong). An
        encrypted archive opened without one is still returned: the panel asks
        for the password when it enters it.
        """
        pw = normalize_password(password)
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
        elif pw is not None and self.is_encrypted(str(path)):
            if not self.check_password(str(path), pw):
                raise PermissionError(f"Wrong password for {path.name}")
        if pw is not None:
            self.set_password(str(path), pw)
        return VfsPath(scheme="zip", root=str(path), parts=())

    # -- write (append-only) ----------------------------------------------

    def open_write(
        self, loc: VfsPath, *, size_hint: int | None = None, overwrite: bool = False
    ) -> BinaryIO:
        """Return a writer for ``loc``.

        Default is append-only: the writer streams the member straight into the
        archive (compressing per chunk), and an existing member name is refused
        (``FileExistsError`` raised from the writer's constructor). With
        ``overwrite=True`` (editing a member in place) the writer buffers in
        memory and rewrites the archive with that member replaced on close —
        fine for the small bytes the editor deals with.
        """
        inner = "/".join(loc.parts)
        if not inner:
            raise OSError("cannot write the archive root as a member")
        if Path(loc.root).exists():
            self._require_unlocked(loc.root)
        return _ZipMemberWriter(
            loc.root, inner, overwrite=overwrite, pwd=self._pwd(loc.root)
        )

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
        self._require_unlocked(parent.root)
        with _open_zip(parent.root, "a", self._pwd(parent.root)) as zf:
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
        pwd = self._pwd(archive)
        tmp = archive + ".tmp"
        try:
            with _open_zip(archive, "r", pwd) as src, \
                 _open_zip(tmp, "w", pwd, compression=zipfile.ZIP_DEFLATED) as dst:
                for item in src.infolist():
                    name = item.filename.rstrip("/")
                    if name in drop or item.filename.startswith(prefixes):
                        continue
                    dst.writestr(item, src.read(item.filename))
            os.replace(tmp, archive)
        except (OSError, RuntimeError) as exc:
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
        skip_existing: bool | str = False,
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
