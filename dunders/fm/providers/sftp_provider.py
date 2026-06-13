"""SftpProvider — browse and transfer over SFTP (SSH) via paramiko.

The secure sibling of :mod:`~dunders.fm.providers.ftp_provider`: same
``resolve_target`` "open a connection" model, but over SSH, with key *or*
password auth and a structured listing (``listdir_attr`` gives type/size/mtime
in one call — no MLSD/LIST guessing).

paramiko is an optional dependency; ``default_registry`` only registers this
provider when it imports, so ``sftp:`` simply doesn't appear otherwise.

Addressing
----------
``VfsPath(scheme="sftp", root="user@host:port", parts=("dir", "file"))``.
Credentials stay out of the locator and live in the provider, keyed by ``root``.

Auth
----
``sftp:user@host`` tries SSH agent + default keys (~/.ssh/id_*) automatically;
``needs_password`` returns True (→ the app prompts) only when no key is
available. ``user:pass@host`` / a prompted password are used directly.

Caveats (v1): host keys are auto-accepted (no known_hosts verification yet);
listing is synchronous on the UI thread (briefly blocks); one connection per
root, serialised by a lock.
"""

from __future__ import annotations

import io
import socket
import stat
import threading
from pathlib import Path
from typing import BinaryIO

import paramiko

from dunders.core.vfs import VfsPath
from dunders.core.vfs.provider import ProgressCallback
from dunders.fm.actions import OpError, OpResult
from dunders.fm.file_entry import FileEntry


__all__ = ["SftpProvider"]

_DEFAULT_PORT = 22
_TIMEOUT = 15
_KEY_NAMES = ("id_rsa", "id_ed25519", "id_ecdsa", "id_dsa")


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


def _connect_error(exc: Exception, host: str, port: int, user: str) -> str:
    if isinstance(exc, paramiko.AuthenticationException):
        return f"SSH auth failed for {user!r} (wrong password, or no authorised key?)"
    if isinstance(exc, socket.gaierror):
        return f"Unknown host: {host}"
    if isinstance(exc, ConnectionRefusedError):
        return f"Connection refused by {host}:{port} (wrong port or server down?)"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return f"Connection to {host}:{port} timed out (host unreachable or blocked?)"
    if isinstance(exc, paramiko.SSHException):
        return f"SSH error connecting to {host}:{port}: {exc}"
    return f"SFTP error connecting to {host}:{port}: {exc}"


def _have_local_keys() -> bool:
    """Whether SSH key auth could plausibly work (agent has keys, or a default
    key file exists) — used to decide whether to pre-prompt for a password."""
    sshdir = Path.home() / ".ssh"
    if any((sshdir / n).exists() for n in _KEY_NAMES):
        return True
    try:
        return bool(paramiko.Agent().get_keys())
    except Exception:
        return False


class _SftpWriter(io.BytesIO):
    """Buffers bytes and uploads them on close."""

    def __init__(self, sftp, lock: threading.Lock, remote: str) -> None:
        super().__init__()
        self._sftp = sftp
        self._lock = lock
        self._remote = remote
        self._flushed = False

    def close(self) -> None:
        if not self._flushed and not self.closed:
            self._flushed = True
            data = self.getvalue()
            with self._lock:
                self._sftp.putfo(io.BytesIO(data), self._remote)
        super().close()


class SftpProvider:
    """Read/write ``VfsProvider`` for SFTP servers (via paramiko)."""

    scheme = "sftp"
    display_name = "SFTP"
    capabilities = frozenset({"read", "write", "stream"})

    def __init__(self) -> None:
        self._creds: dict[str, tuple[str, int, str, str | None]] = {}
        self._clients: dict[str, tuple[paramiko.SSHClient, object]] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    # -- connection management --------------------------------------------

    def _lock_for(self, root: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(root, threading.Lock())

    def _connect(self, root: str):
        host, port, user, passwd = self._creds[root]
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            host, port=port, username=user,
            password=passwd or None,
            look_for_keys=True, allow_agent=True, timeout=_TIMEOUT,
        )
        return client, client.open_sftp()

    def _sftp(self, root: str):
        if root not in self._creds:
            raise OSError(f"no SFTP connection for {root!r} (open it first)")
        with self._guard:
            entry = self._clients.get(root)
        if entry is not None:
            transport = entry[0].get_transport()
            if transport is not None and transport.is_active():
                return entry[1]
        entry = self._connect(root)
        with self._guard:
            self._clients[root] = entry
        return entry[1]

    @staticmethod
    def _remote(loc: VfsPath) -> str:
        return "/" + "/".join(loc.parts)

    # -- prefix target ("sftp:user@host/path" opens a connection) ----------

    def needs_password(self, spec: str) -> bool:
        _host, _port, user, passwd, _path = _parse_spec(spec)
        if user is None or passwd is not None:
            return False
        return not _have_local_keys()  # only prompt when no key can be tried

    def resolve_target(
        self, spec: str, *, base: VfsPath, password: str | None = None
    ) -> VfsPath | None:
        host, port, user, passwd, path = _parse_spec(spec)
        if not host:
            return None
        if not 0 < port <= 65535:
            raise OSError(f"Invalid port {port} (must be 1-65535)")
        if password is not None:
            passwd = password
        login_user = user or "root"
        root = _canonical_root(host, port, user)
        self._creds[root] = (host, port, login_user, passwd)
        try:
            self._sftp(root)
        except Exception as exc:
            self._creds.pop(root, None)
            raise OSError(_connect_error(exc, host, port, login_user)) from exc
        parts = tuple(p for p in path.split("/") if p)
        return VfsPath(scheme="sftp", root=root, parts=parts)

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
        sftp = self._sftp(loc.root)
        lock = self._lock_for(loc.root)
        with lock:
            attrs = sftp.listdir_attr(self._remote(loc))
        for a in attrs:
            name = a.filename
            if name in (".", "..") or (not show_hidden and name.startswith(".")):
                continue
            entries.append(FileEntry(
                loc=loc.child(name),
                name=name,
                size=int(a.st_size or 0),
                mtime=float(a.st_mtime or 0),
                is_dir=stat.S_ISDIR(a.st_mode or 0),
            ))
        return entries

    def _parent_entry(self, loc: VfsPath) -> FileEntry:
        parent = loc.parent or loc
        return FileEntry(loc=parent, name="..", size=0, mtime=0.0, is_dir=True)

    def is_dir(self, loc: VfsPath) -> bool:
        if not loc.parts:
            return True
        sftp = self._sftp(loc.root)
        lock = self._lock_for(loc.root)
        with lock:
            try:
                return stat.S_ISDIR(sftp.stat(self._remote(loc)).st_mode or 0)
            except OSError:
                return False

    def open_read(self, loc: VfsPath) -> BinaryIO:
        sftp = self._sftp(loc.root)
        lock = self._lock_for(loc.root)
        with lock:
            with sftp.open(self._remote(loc), "rb") as fh:
                data = fh.read()
        return io.BytesIO(data)

    def open_write(
        self, loc: VfsPath, *, size_hint: int | None = None, overwrite: bool = False
    ) -> BinaryIO:
        sftp = self._sftp(loc.root)
        lock = self._lock_for(loc.root)
        remote = self._remote(loc)
        if not overwrite:
            with lock:
                try:
                    sftp.stat(remote)
                    raise FileExistsError(f"{remote} already exists on server")
                except FileNotFoundError:
                    pass
        return _SftpWriter(sftp, lock, remote)

    def mkdir(self, parent: VfsPath, name: str) -> OpResult:
        sftp = self._sftp(parent.root)
        lock = self._lock_for(parent.root)
        remote = "/" + "/".join((*parent.parts, name))
        with lock:
            try:
                sftp.mkdir(remote)
            except OSError:
                pass  # already exists / tolerated, like the other providers
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
        sftp = self._sftp(targets[0].root)
        lock = self._lock_for(targets[0].root)
        with lock:
            for i, t in enumerate(targets, 1):
                if cancel_event is not None and cancel_event.is_set():
                    result.cancelled = True
                    break
                try:
                    self._delete_one(sftp, self._remote(t))
                except OSError as exc:
                    result.errors.append(OpError(loc=t, reason=str(exc)))
                if on_progress is not None:
                    on_progress(i, len(targets))
        return result

    def _delete_one(self, sftp, remote: str) -> None:
        if stat.S_ISDIR(sftp.stat(remote).st_mode or 0):
            for a in sftp.listdir_attr(remote):
                self._delete_one(sftp, f"{remote}/{a.filename}")
            sftp.rmdir(remote)
        else:
            sftp.remove(remote)

    # No server-side copy; the engine streams via open_read/open_write.
    def copy_within(self, sources, dest, *, rename_to=None, on_progress=None,
                    cancel_event=None) -> OpResult | None:
        return None

    def move_within(self, sources, dest, *, rename_to=None, on_progress=None,
                    cancel_event=None) -> OpResult | None:
        return None
