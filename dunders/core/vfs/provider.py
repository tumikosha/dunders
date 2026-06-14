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
from dataclasses import dataclass
from typing import TYPE_CHECKING, BinaryIO, Protocol, runtime_checkable

from dunders.core.vfs.locator import VfsPath

if TYPE_CHECKING:
    from dunders.fm.actions import OpResult
    from dunders.fm.file_entry import FileEntry


__all__ = ["VfsProvider", "ProgressCallback", "TargetResolver", "ProviderAction",
           "ProviderActions", "ProviderColumn", "ProviderColumns"]

ProgressCallback = Callable[[int, int], None]


@dataclass(frozen=True)
class ProviderAction:
    """A verb a provider exposes on its entries (e.g. start a container).

    ``run`` does the work for the given locators and returns an ``OpResult``;
    it may be slow (callers run it on a worker thread). ``applies_to`` decides
    whether the action is offered for a given ``FileEntry`` (e.g. Start only
    when a container is stopped). ``icon`` is a glyph shown in menus and as a
    clickable button.
    """

    id: str
    label: str
    run: Callable[[list[VfsPath]], "OpResult"]
    icon: str = ""
    hotkey: str | None = None
    applies_to: Callable[["FileEntry"], bool] = lambda e: True


@runtime_checkable
class ProviderActions(Protocol):
    """Optional capability: a provider that declares verbs on its entries.
    Checked structurally (``getattr``/``isinstance``), like ``TargetResolver``."""

    scheme: str

    def actions(self) -> "list[ProviderAction]": ...


@dataclass(frozen=True)
class ProviderColumn:
    """An extra panel column a provider contributes for its listings (e.g. a
    Docker "S" state column). Replaces the default Size/Date columns when a
    provider declares any. ``value`` renders the cell text (``width`` cells
    wide, centred); ``sort_key`` is used when the user sorts by this column
    (click on its header). ``label`` is the header text."""

    key: str
    label: str
    width: int
    value: Callable[["FileEntry"], str]
    sort_key: Callable[["FileEntry"], object]


@runtime_checkable
class ProviderColumns(Protocol):
    """Optional capability: a provider that contributes panel columns for a
    given location (so it can show columns only where they make sense, e.g.
    Docker's container index but not inside a container). Checked structurally."""

    scheme: str

    def columns(self, loc: VfsPath) -> "list[ProviderColumn]": ...


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

    def open_write(
        self, loc: VfsPath, *, size_hint: int | None = None, overwrite: bool = False
    ) -> BinaryIO:
        """Open a member/file for writing. ``overwrite=True`` replaces an
        existing target (used when editing a member in place); the default
        refuses an existing member so copies never clobber silently."""
        ...

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


@runtime_checkable
class TargetResolver(Protocol):
    """Optional capability: turn a typed ``<scheme>:<spec>`` destination into a
    write-target locator, creating the archive/connection if needed.

    A provider *declares its prefix* simply as its ``scheme`` and opts into
    "create on copy" by implementing this. When an F5 copy destination starts
    with ``<scheme>:``, the app hands the part after the colon to the matching
    provider's ``resolve_target`` and copies the selection into the returned
    locator (then opens it in the panel). Examples:

    - ``zip:backup.zip`` → a new ``zip`` archive at ``<base>/backup.zip``.
    - ``ftp:user@host/path`` → an opened ``ftp`` connection rooted there.

    Kept separate from :class:`VfsProvider` (not all providers create targets),
    so it is checked structurally via ``getattr``/``isinstance``.
    """

    scheme: str

    def resolve_target(
        self, spec: str, *, base: VfsPath, password: str | None = None
    ) -> VfsPath | None:
        """``spec`` is the text after ``<scheme>:``. ``base`` is the destination
        panel's location (where a relative target is created). ``password`` is a
        prompted secret for providers that need one (see ``needs_password``);
        others ignore it. Return the target locator, or ``None`` if this
        provider can't take ``spec`` here."""
        ...
