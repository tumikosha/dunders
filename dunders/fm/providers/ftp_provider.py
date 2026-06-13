"""FtpProvider — browse and transfer over FTP via the stdlib :mod:`ftplib`.

The first *network* VFS provider, and the proof that ``resolve_target`` models
"open a connection" as well as "create an archive": typing ``ftp:user@host/path``
in the "_" menu (or an F5 destination) connects and mounts the remote tree in a
panel.

Addressing
----------
``VfsPath(scheme="ftp", root="user@host:port", parts=("dir", "file"))`` — the
``root`` identifies the connection; credentials are kept out of the locator
(it is shown in the UI) and stored in the provider, keyed by ``root``, when
``resolve_target`` first parses them.

Scope / caveats (v1)
--------------------
- Plain FTP (``ftplib``); no SFTP (that needs paramiko) and no password prompt
  yet — credentials come inline as ``user:pass@host`` (anonymous if no user).
- Listing uses MLSD (RFC 3659; supported by modern servers and pyftpdlib).
- One cached connection per ``root``, serialised by a lock — ftplib is not
  thread-safe, and copy/delete run on worker threads while scan runs on the UI
  thread. A transfer therefore briefly blocks a concurrent listing of the same
  host. Async listing is a follow-up.
"""

from __future__ import annotations

import ftplib
import io
import socket
import threading
import time
from typing import BinaryIO

from dunders.core.vfs import VfsPath
from dunders.core.vfs.provider import ProgressCallback
from dunders.fm.actions import OpError, OpResult
from dunders.fm.file_entry import FileEntry


__all__ = ["FtpProvider"]

_DEFAULT_PORT = 21
_TIMEOUT = 15  # seconds — connect runs on the UI thread, so keep it snappy


def _connect_error(exc: Exception, host: str, port: int, user: str) -> str:
    """Turn a raw connection/login exception into a user-facing reason."""
    if isinstance(exc, ftplib.error_perm):
        return f"FTP login failed for {user!r}: {str(exc).strip()}"
    if isinstance(exc, socket.gaierror):
        return f"Unknown host: {host}"
    if isinstance(exc, ConnectionRefusedError):
        return f"Connection refused by {host}:{port} (wrong port or server down?)"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return f"Connection to {host}:{port} timed out (host unreachable or blocked?)"
    return f"FTP error connecting to {host}:{port}: {exc}"


def _parse_spec(spec: str) -> tuple[str, int, str | None, str | None, str]:
    """Parse ``[//][user[:pass]@]host[:port][/path]`` → (host, port, user, pass, path)."""
    s = spec.strip()
    if s.startswith("//"):
        s = s[2:]
    netloc, _, path = s.partition("/")
    user = passwd = None
    if "@" in netloc:
        cred, _, host = netloc.rpartition("@")
        user, sep, p = cred.partition(":")
        passwd = p if sep else None
    else:
        host = netloc
    port = _DEFAULT_PORT
    if ":" in host:
        host, _, p = host.partition(":")
        if p.isdigit():
            port = int(p)
    return host, port, (user or None), passwd, path


def _canonical_root(host: str, port: int, user: str | None) -> str:
    who = f"{user}@" if user else ""
    return f"{who}{host}:{port}"


def _parse_modify(value: str) -> float:
    try:
        return time.mktime(time.strptime(value[:14], "%Y%m%d%H%M%S"))
    except (ValueError, OverflowError):
        return 0.0


def _parse_list_line(line: str) -> tuple[str, bool, int] | None:
    """Parse one Unix ``ls -l``-style LIST line → (name, is_dir, size).

    The fallback for servers that don't support MLSD. Windows/IIS listings have
    a different shape and are not parsed here (returns None → skipped)."""
    parts = line.split(None, 8)
    if len(parts) < 9 or parts[0][:1] not in "dl-bcps":
        return None
    is_dir = parts[0].startswith("d")
    try:
        size = int(parts[4])
    except ValueError:
        size = 0
    name = parts[8]
    if " -> " in name:  # symlink "name -> target"
        name = name.split(" -> ", 1)[0]
    return name, is_dir, size


class _FtpWriter(io.BytesIO):
    """Buffers bytes and STORs them to the remote path on close."""

    def __init__(self, conn: ftplib.FTP, lock: threading.Lock, remote: str) -> None:
        super().__init__()
        self._conn = conn
        self._lock = lock
        self._remote = remote
        self._flushed = False

    def close(self) -> None:
        if not self._flushed and not self.closed:
            self._flushed = True
            data = self.getvalue()
            with self._lock:
                self._conn.storbinary(f"STOR {self._remote}", io.BytesIO(data))
        super().close()


class FtpProvider:
    """Read/write ``VfsProvider`` for FTP servers."""

    scheme = "ftp"
    display_name = "FTP"
    capabilities = frozenset({"read", "write", "stream", "slow"})

    def __init__(self) -> None:
        self._creds: dict[str, tuple[str, int, str, str]] = {}
        self._conns: dict[str, ftplib.FTP] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()  # guards the dicts above

    # -- connection management --------------------------------------------

    def _lock_for(self, root: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(root, threading.Lock())

    def _connect(self, root: str) -> ftplib.FTP:
        host, port, user, passwd = self._creds[root]
        ftp = ftplib.FTP()
        ftp.connect(host, port, timeout=_TIMEOUT)
        ftp.login(user, passwd)
        # Binary mode throughout: file ops want it, and SIZE (the open_write
        # existence check) is refused by many servers in ASCII mode.
        ftp.voidcmd("TYPE I")
        return ftp

    def _conn(self, root: str) -> ftplib.FTP:
        if root not in self._creds:
            raise OSError(f"no FTP connection for {root!r} (open it first)")
        with self._guard:
            conn = self._conns.get(root)
        if conn is not None:
            try:
                conn.voidcmd("NOOP")  # still alive?
                return conn
            except Exception:
                pass  # dropped — reconnect below
        conn = self._connect(root)
        with self._guard:
            self._conns[root] = conn
        return conn

    @staticmethod
    def _remote(loc: VfsPath) -> str:
        return "/" + "/".join(loc.parts)

    # -- prefix target ("ftp:user@host/path" opens a connection) ----------

    def needs_password(self, spec: str) -> bool:
        """True when the spec names a user but no inline password — the app
        should prompt for one before opening (anonymous needs no prompt)."""
        _host, _port, user, passwd, _path = _parse_spec(spec)
        return user is not None and passwd is None

    def resolve_target(
        self, spec: str, *, base: VfsPath, password: str | None = None
    ) -> VfsPath | None:
        host, port, user, passwd, path = _parse_spec(spec)
        if not host:
            return None
        if not 0 < port <= 65535:
            # An out-of-range port surfaces as a misleading "unknown host" from
            # the socket layer — flag it precisely instead.
            raise OSError(f"Invalid port {port} (must be 1-65535)")
        if password is not None:
            passwd = password  # prompted password overrides / fills in
        login_user = user or "anonymous"
        login_pass = passwd or ""
        root = _canonical_root(host, port, user)
        self._creds[root] = (host, port, login_user, login_pass)
        # Validate by connecting now (the "_" menu would otherwise navigate into
        # a broken panel). Unlike a None return (spec not interpretable), an
        # operational failure raises with a specific reason for the toast.
        try:
            self._conn(root)
        except Exception as exc:
            self._creds.pop(root, None)
            raise OSError(_connect_error(exc, host, port, login_user)) from exc
        parts = tuple(p for p in path.split("/") if p)
        return VfsPath(scheme="ftp", root=root, parts=parts)

    # -- VfsProvider ------------------------------------------------------

    def scan(
        self,
        loc: VfsPath,
        *,
        show_hidden: bool = False,
        include_parent: bool = True,
    ) -> list[FileEntry]:
        entries: list[FileEntry] = []
        if include_parent:
            entries.append(self._parent_entry(loc))
        conn = self._conn(loc.root)
        lock = self._lock_for(loc.root)
        remote = self._remote(loc)
        with lock:
            rows = self._raw_list(conn, remote)
        for name, is_dir, size, mtime in rows:
            if name in (".", "..") or (not show_hidden and name.startswith(".")):
                continue
            entries.append(FileEntry(
                loc=loc.child(name), name=name, size=size, mtime=mtime, is_dir=is_dir,
            ))
        return entries

    @staticmethod
    def _raw_list(conn: ftplib.FTP, remote: str) -> list[tuple[str, bool, int, float]]:
        """List ``remote`` as (name, is_dir, size, mtime). Tries MLSD (RFC 3659),
        falls back to LIST parsing for servers that lack it. Best-effort: any
        listing failure yields an empty result rather than raising, so the panel
        degrades to an empty directory instead of crashing."""
        try:
            rows = []
            for name, facts in conn.mlsd(remote):
                typ = facts.get("type", "")
                if typ in ("cdir", "pdir"):
                    continue
                rows.append((
                    name, typ == "dir",
                    int(facts.get("size", 0) or 0),
                    _parse_modify(facts.get("modify", "")),
                ))
            return rows
        except ftplib.all_errors:
            pass  # MLSD unsupported / failed → try LIST
        try:
            lines: list[str] = []
            conn.retrlines(f"LIST {remote}", lines.append)
            rows = []
            for line in lines:
                parsed = _parse_list_line(line)
                if parsed is not None:
                    rows.append((*parsed, 0.0))
            return rows
        except ftplib.all_errors:
            return []

    def _parent_entry(self, loc: VfsPath) -> FileEntry:
        # '..' goes up within the remote tree; at the connection root it has no
        # local parent, so it points at the connection root itself (a no-op up).
        parent = loc.parent or loc
        return FileEntry(loc=parent, name="..", size=0, mtime=0.0, is_dir=True)

    def is_dir(self, loc: VfsPath) -> bool:
        if not loc.parts:
            return True  # connection root
        parent = loc.parent
        assert parent is not None
        name = loc.parts[-1]
        return any(e.name == name and e.is_dir
                   for e in self.scan(parent, include_parent=False))

    def open_read(self, loc: VfsPath) -> BinaryIO:
        conn = self._conn(loc.root)
        lock = self._lock_for(loc.root)
        buf = io.BytesIO()
        with lock:
            conn.retrbinary(f"RETR {self._remote(loc)}", buf.write)
        buf.seek(0)
        return buf

    def open_write(
        self, loc: VfsPath, *, size_hint: int | None = None, overwrite: bool = False
    ) -> BinaryIO:
        conn = self._conn(loc.root)
        lock = self._lock_for(loc.root)
        remote = self._remote(loc)
        if not overwrite:
            with lock:
                try:
                    conn.size(remote)
                    raise FileExistsError(f"{remote} already exists on server")
                except ftplib.error_perm:
                    pass  # not found -> ok to write
        return _FtpWriter(conn, lock, remote)

    def mkdir(self, parent: VfsPath, name: str) -> OpResult:
        conn = self._conn(parent.root)
        lock = self._lock_for(parent.root)
        remote = "/" + "/".join((*parent.parts, name))
        with lock:
            try:
                conn.mkd(remote)
            except ftplib.error_perm:
                pass  # already exists / tolerated, like zip/7z mkdir
        return OpResult()

    def delete(
        self,
        targets: list[VfsPath],
        *,
        on_progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> OpResult:
        result = OpResult()
        if not targets:
            return result
        conn = self._conn(targets[0].root)
        lock = self._lock_for(targets[0].root)
        with lock:
            for i, t in enumerate(targets, 1):
                if cancel_event is not None and cancel_event.is_set():
                    result.cancelled = True
                    break
                try:
                    self._delete_one(conn, self._remote(t))
                except ftplib.all_errors as exc:
                    result.errors.append(OpError(loc=t, reason=str(exc)))
                if on_progress is not None:
                    on_progress(i, len(targets))
        return result

    def _delete_one(self, conn: ftplib.FTP, remote: str) -> None:
        try:
            conn.delete(remote)  # a file
            return
        except ftplib.error_perm:
            pass  # likely a directory — recurse
        for name, is_dir, _size, _mtime in self._raw_list(conn, remote):
            if name in (".", ".."):
                continue
            child = f"{remote}/{name}"
            if is_dir:
                self._delete_one(conn, child)
            else:
                conn.delete(child)
        conn.rmd(remote)

    # Intra-FTP fast paths: none (server-side copy isn't portable) — the engine
    # streams via open_read/open_write.
    def copy_within(self, sources, dest, *, rename_to=None, on_progress=None,
                    cancel_event=None) -> OpResult | None:
        return None

    def move_within(self, sources, dest, *, rename_to=None, on_progress=None,
                    cancel_event=None) -> OpResult | None:
        return None
