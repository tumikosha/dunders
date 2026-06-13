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

    def open_write(self, loc: VfsPath, *, size_hint: int | None = None) -> BinaryIO:
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
        cancel_event: threading.Event | None = None,
    ) -> actions.OpResult:
        return actions.copy_paths(
            [s.to_local() for s in sources],
            dest.to_local(),
            rename_to=rename_to,
            on_progress=on_progress,
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
    from dunders.fm.providers.zip_provider import ZipProvider

    reg = VfsRegistry()
    reg.register(LocalProvider())
    reg.register(ZipProvider())
    return reg
