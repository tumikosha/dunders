"""VfsPath — a scheme-agnostic locator for the virtual filesystem.

The file manager historically addresses everything with :class:`pathlib.Path`.
That hard-wires it to the local filesystem: an archive, an SFTP server, or a
docker container has no ``Path``. ``VfsPath`` is the locator that replaces a
bare ``Path`` in VFS contracts so a panel can walk *any* source uniformly.

Model
-----
A locator is ``(scheme, root, parts)``:

* ``scheme`` — which provider owns it: ``"file"``, ``"zip"``, ``"sftp"``,
  ``"docker"``, ``"sqlite"``, ``"api"`` …
* ``root`` — the source identifier within that scheme: the filesystem anchor
  (``"/"``) for ``file``, the path to the ``.zip`` for ``zip``,
  ``"user@host"`` for ``sftp``, a container id for ``docker``.
* ``parts`` — the path *inside* that source, one tuple element per segment.

Navigation (:meth:`child`, :attr:`parent`, :attr:`name`) is provider-neutral:
it only ever touches ``parts``. ``parent`` is ``None`` at the source root —
leaving the source entirely (e.g. stepping out of an archive) is panel policy,
not the locator's concern.

The ``file`` scheme bridges to/from :mod:`pathlib` via :meth:`local` /
:meth:`to_local`, so existing ``scan.py`` / ``actions.py`` code keeps working
behind the new contract. ``to_local`` raises for every other scheme.

Note: URI handling targets POSIX locators (the app's TUI runs on macOS/Linux).
Windows drive anchors are preserved by the ``file`` bridge but not specially
parsed in :meth:`parse`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import unquote


__all__ = ["VfsPath"]


@dataclass(frozen=True)
class VfsPath:
    scheme: str
    root: str
    parts: tuple[str, ...] = ()

    # -- navigation (scheme-neutral) --------------------------------------

    @property
    def name(self) -> str:
        """Last path segment, or the source ``root`` when at the source root."""
        return self.parts[-1] if self.parts else self.root

    @property
    def is_source_root(self) -> bool:
        return not self.parts

    @property
    def parent(self) -> VfsPath | None:
        """Enclosing locator, or ``None`` at the source root."""
        if self.parts:
            return replace(self, parts=self.parts[:-1])
        return None

    def child(self, name: str) -> VfsPath:
        return replace(self, parts=self.parts + (name,))

    # -- local-filesystem bridge ------------------------------------------

    @classmethod
    def local(cls, p: Path | str) -> VfsPath:
        """Wrap a local path as a ``file``-scheme locator."""
        p = Path(p)
        anchor = p.anchor
        if anchor:
            rest = p.parts[1:] if p.parts and p.parts[0] == anchor else p.parts
            return cls(scheme="file", root=anchor, parts=tuple(rest))
        return cls(scheme="file", root="", parts=tuple(p.parts))

    def to_local(self) -> Path:
        """Convert back to a :class:`Path`. Raises for non-``file`` schemes."""
        if self.scheme != "file":
            raise ValueError(f"to_local() is only valid for file scheme, got {self.scheme!r}")
        if self.root:
            return Path(self.root, *self.parts)
        return Path(*self.parts) if self.parts else Path(".")

    # -- URI serialisation -------------------------------------------------

    def as_uri(self) -> str:
        if self.scheme == "file":
            return self.to_local().as_uri()
        if self.parts:
            return f"{self.scheme}://{self.root}!/{'/'.join(self.parts)}"
        return f"{self.scheme}://{self.root}"

    @classmethod
    def parse(cls, uri: str) -> VfsPath:
        scheme, sep, rest = uri.partition("://")
        if not sep:
            raise ValueError(f"not a VfsPath URI: {uri!r}")
        if scheme == "file":
            return cls.local(unquote(rest))
        root, bang, inner = rest.partition("!")
        parts = tuple(seg for seg in inner.lstrip("/").split("/") if seg) if bang else ()
        return cls(scheme=scheme, root=root, parts=parts)

    def __str__(self) -> str:
        return self.as_uri()
