"""LocalProvider — the ``file`` scheme backed by the local filesystem.

This is the reference :class:`~dunders.core.vfs.provider.VfsProvider`: it does
nothing new, it simply wraps the existing ``dunders.fm.scan`` /
``dunders.fm.actions`` functions behind the provider contract. Behaviour is
unchanged — ``file → file`` operations go straight to ``copy_paths`` /
``move_paths`` / ``delete_paths`` / ``mkdir_at`` — so introducing the
abstraction carries zero regression for local use.

It is the proof that the provider protocol fits the real code; the first
*foreign* provider (a read-only zip) follows in Phase 1.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import BinaryIO

from dunders.core.vfs import VfsPath, VfsRegistry
from dunders.core.vfs.provider import ProgressCallback
from dunders.fm import actions
from dunders.fm.file_entry import FileEntry
from dunders.fm.scan import scan_dir


__all__ = ["LocalProvider", "default_registry"]


class LocalProvider:
    """``VfsProvider`` for the local filesystem (structural conformance)."""

    scheme = "file"
    capabilities = frozenset({"read", "write", "stream", "random_access"})
    # Make "Local files" an entry in the "_" dunder menu — a one-keystroke way
    # back to the local filesystem from any other dunder. Empty spec = home.
    display_name = "Local files"
    accepts_empty_open = True
    open_placeholder = "path (empty = home directory)"

    def resolve_target(
        self, spec: str, *, base: VfsPath, password: str | None = None
    ) -> VfsPath | None:
        spec = spec.strip()
        p = Path(spec).expanduser() if spec else Path.home()
        if not p.is_absolute():
            anchor = base.to_local() if base.scheme == "file" else Path.home()
            p = anchor / p
        try:
            p = p.resolve()
        except OSError:
            pass
        if not p.is_dir():
            raise OSError(f"Not a directory: {p}")
        return VfsPath.local(p)

    def scan(
        self,
        loc: VfsPath,
        *,
        show_hidden: bool = False,
        include_parent: bool = True,
    ) -> list[FileEntry]:
        return scan_dir(
            loc.to_local(),
            show_hidden=show_hidden,
            include_parent=include_parent,
        )

    def is_dir(self, loc: VfsPath) -> bool:
        return loc.to_local().is_dir()

    def open_read(self, loc: VfsPath) -> BinaryIO:
        return open(loc.to_local(), "rb")

    def open_write(
        self, loc: VfsPath, *, size_hint: int | None = None, overwrite: bool = False
    ) -> BinaryIO:
        # Local files always truncate on "wb"; overwrite is implicit.
        return open(loc.to_local(), "wb")

    def mkdir(self, parent: VfsPath, name: str) -> actions.OpResult:
        return actions.mkdir_at(parent.to_local(), name)

    def delete(
        self,
        targets: list[VfsPath],
        *,
        on_progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> actions.OpResult:
        return actions.delete_paths(
            [t.to_local() for t in targets],
            on_progress=on_progress,
            cancel_event=cancel_event,
        )

    def copy_within(
        self,
        sources: list[VfsPath],
        dest: VfsPath,
        *,
        rename_to: str | None = None,
        on_progress: ProgressCallback | None = None,
        on_status: actions.StatusCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> actions.OpResult:
        return actions.copy_paths(
            [s.to_local() for s in sources],
            dest.to_local(),
            rename_to=rename_to,
            on_progress=on_progress,
            on_status=on_status,
            cancel_event=cancel_event,
        )

    def move_within(
        self,
        sources: list[VfsPath],
        dest: VfsPath,
        *,
        rename_to: str | None = None,
        on_progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> actions.OpResult:
        return actions.move_paths(
            [s.to_local() for s in sources],
            dest.to_local(),
            rename_to=rename_to,
            on_progress=on_progress,
            cancel_event=cancel_event,
        )


def default_registry() -> VfsRegistry:
    """A fresh registry with the built-in providers wired in.

    Returns a new instance per call — providers are stateless (bar per-archive
    caches), so panels do not need to share one, and tests stay isolated.
    """
    from dunders.fm.providers.ftp_provider import FtpProvider
    from dunders.fm.providers.sevenzip_provider import SevenZipProvider, find_7z
    from dunders.fm.providers.zip_provider import ZipProvider

    reg = VfsRegistry()
    reg.register(LocalProvider())
    reg.register(ZipProvider())
    reg.register(FtpProvider())  # network provider; opened via "_" menu / ftp: prefix
    # SFTP needs paramiko (optional dep); register only when it imports so the
    # "sftp:" scheme simply doesn't appear otherwise.
    try:
        from dunders.fm.providers.sftp_provider import SftpProvider
        reg.register(SftpProvider())
    except ImportError:
        pass
    # Docker is browsed via its CLI; only offer the scheme when the binary is
    # present and the daemon answers, like the 7z provider.
    from dunders.fm.providers.docker_provider import DockerProvider, docker_available
    if docker_available():
        reg.register(DockerProvider())
    # 7z is browsed via the external CLI; only offer the scheme when a binary
    # is present, so the panel never tries to enter a .7z it cannot open.
    if find_7z() is not None:
        reg.register(SevenZipProvider())
    return reg
