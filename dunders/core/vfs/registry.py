"""VfsRegistry — maps a URI scheme to the provider that serves it.

The panel never imports a concrete provider; it asks the registry for the one
owning ``loc.scheme``. Registering a new backend (zip, sftp, docker) is the
single wiring point that turns it into a navigable panel.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dunders.core.vfs.locator import VfsPath

if TYPE_CHECKING:
    from dunders.core.vfs.provider import VfsProvider


__all__ = ["VfsRegistry"]


class VfsRegistry:
    def __init__(self) -> None:
        self._by_scheme: dict[str, VfsProvider] = {}

    def register(self, provider: VfsProvider) -> None:
        """Add (or replace) the provider for ``provider.scheme``."""
        self._by_scheme[provider.scheme] = provider

    def for_scheme(self, scheme: str) -> VfsProvider:
        try:
            return self._by_scheme[scheme]
        except KeyError:
            raise KeyError(f"no VFS provider registered for scheme {scheme!r}") from None

    def resolve(self, loc: VfsPath) -> VfsProvider:
        """Provider that owns ``loc``."""
        return self.for_scheme(loc.scheme)

    def schemes(self) -> frozenset[str]:
        return frozenset(self._by_scheme)
