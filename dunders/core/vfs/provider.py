"""VfsProvider — the contract every filesystem backend implements.

A provider owns one ``scheme`` (``file``, ``zip``, ``sftp``, ``docker`` …) and
turns a :class:`VfsPath` into listings, byte streams, and mutations. The panel
and the copy engine talk only to this protocol, so a new backend (an archive,
an SFTP server, a JSON API) becomes a panel by implementing it — nothing in the
UI changes.

Progress / cancellation deliberately mirror :mod:`dunders.fm.actions`
(``on_progress(index, total)`` + a :class:`threading.Event`) so the existing
worker-thread + ProgressDialog plumbing in ``app.py`` is reused unchanged.

Layering note: ``FileEntry`` and ``OpResult`` still live under ``dunders.fm``.
They are fundamentally VFS data types and are expected to migrate into
``dunders.core`` in a later step; until then they are referenced here only
under ``TYPE_CHECKING`` (annotations are strings via ``from __future__``), so
there is no runtime ``core → fm`` import cycle.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, BinaryIO, Protocol, runtime_checkable

from dunders.core.vfs.locator import VfsPath

if TYPE_CHECKING:
    from dunders.fm.actions import OpResult
    from dunders.fm.file_entry import FileEntry


__all__ = ["VfsProvider", "ProgressCallback"]

ProgressCallback = Callable[[int, int], None]


@runtime_checkable
class VfsProvider(Protocol):
    scheme: str
    capabilities: frozenset[str]   # {"read","write","stream","random_access","watch"}

    def scan(
        self,
        loc: VfsPath,
        *,
        show_hidden: bool = False,
        include_parent: bool = True,
    ) -> list[FileEntry]: ...

    def is_dir(self, loc: VfsPath) -> bool:
        """Whether ``loc`` is a directory. Used by the generic transfer engine
        to decide between recursing and streaming bytes."""
        ...

    def open_read(self, loc: VfsPath) -> BinaryIO: ...

    def open_write(self, loc: VfsPath, *, size_hint: int | None = None) -> BinaryIO: ...

    def mkdir(self, parent: VfsPath, name: str) -> OpResult: ...

    def delete(
        self,
        targets: list[VfsPath],
        *,
        on_progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> OpResult: ...

    # Intra-provider fast paths — optional. The transfer engine falls back to
    # generic streaming when these return ``None``. ``rename_to`` overrides the
    # destination basename when there is exactly one source (copy-with-rename).
    def copy_within(
        self,
        sources: list[VfsPath],
        dest: VfsPath,
        *,
        rename_to: str | None = None,
        on_progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> OpResult | None: ...

    def move_within(
        self,
        sources: list[VfsPath],
        dest: VfsPath,
        *,
        rename_to: str | None = None,
        on_progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> OpResult | None: ...
