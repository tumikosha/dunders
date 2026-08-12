"""MountTable — projects bookmarks onto MCP mount points.

The bookmarks file on disk is the single source of truth; the table re-reads it
when its mtime changes, so mounts reconfigure live without a server restart.
Slow (network) providers connect lazily via resolve_target on first access.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dunders.ai.guardrails import is_ai_allowed
from dunders.config.bookmarks import bookmarks_mtime, bookmarks_path, list_bookmarks
from dunders.core.vfs import VfsPath, VfsRegistry
from dunders.mcp import errors

__all__ = ["Mount", "MountTable"]


@dataclass(frozen=True)
class Mount:
    label: str
    loc: VfsPath
    scheme: str
    display: str
    password: str | None = field(repr=False)


class MountTable:
    def __init__(
        self, registry: VfsRegistry, *, path=None,
        allow: set[str] | None = None, noai_globs: tuple[str, ...] = (),
    ) -> None:
        self._registry = registry
        self._path = path if path is not None else bookmarks_path()
        self._allow = allow
        self._noai_globs = noai_globs
        self._mounts: dict[str, Mount] = {}
        self._connected: set[str] = set()
        self._mtime = -1.0
        self._reload(force=True)

    # --- loading / reload --------------------------------------------------

    def _reload(self, *, force: bool = False) -> None:
        mtime = bookmarks_mtime(self._path)
        if not force and mtime == self._mtime:
            return
        self._mtime = mtime
        fresh: dict[str, Mount] = {}
        for bm in list_bookmarks(self._path):
            label = bm["label"]
            if self._allow is not None and label not in self._allow:
                continue
            if bm.get("mcp") is False:
                continue
            try:
                loc = VfsPath.parse(bm["uri"])
            except ValueError:
                continue
            if not self._ai_ok(loc):
                continue
            fresh[label] = Mount(
                label=label, loc=loc, scheme=loc.scheme,
                display=loc.display(), password=bm.get("password"),
            )
        # Drop connect flags for labels that vanished or whose loc changed.
        self._connected = {
            lbl for lbl in self._connected
            if lbl in fresh and fresh[lbl].loc == self._mounts.get(lbl, fresh[lbl]).loc
        }
        self._mounts = fresh

    def _ai_ok(self, loc: VfsPath) -> bool:
        if loc.scheme != "file":
            return True  # no local .dunders-noai marker to consult
        try:
            return is_ai_allowed(loc.to_local(), cloud=True, globs=self._noai_globs)
        except ValueError:
            return True

    # --- queries -----------------------------------------------------------

    def mounts(self) -> list[Mount]:
        self._reload()
        return [self._mounts[k] for k in sorted(self._mounts)]

    def get(self, label: str) -> Mount:
        self._reload()
        try:
            return self._mounts[label]
        except KeyError:
            raise errors.McpError(
                errors.MOUNT_NOT_FOUND, f"no mount {label!r}"
            ) from None

    def resolve(self, label: str, path: str) -> VfsPath:
        mount = self.get(label)
        loc = mount.loc
        for seg in path.split("/"):
            if seg in ("", "."):
                continue
            if seg == ".." or "/" in seg or "\\" in seg:
                raise errors.McpError(
                    errors.ACCESS_DENIED, "path escapes mount root"
                )
            loc = loc.child(seg)
        if path.startswith("/"):
            raise errors.McpError(errors.ACCESS_DENIED, "absolute path rejected")
        return loc

    # --- lazy connection ---------------------------------------------------

    def connected(self, label: str) -> bool:
        return label in self._connected

    def ensure_connected(self, mount: Mount) -> None:
        if mount.label in self._connected:
            return
        provider = self._registry.resolve(mount.loc)
        if "slow" not in getattr(provider, "capabilities", frozenset()):
            self._connected.add(mount.label)
            return
        resolver = getattr(provider, "resolve_target", None)
        if callable(resolver):
            spec = self._spec_for(mount.loc)
            resolver(spec, base=mount.loc, password=mount.password)
        self._connected.add(mount.label)

    @staticmethod
    def _spec_for(loc: VfsPath) -> str:
        # Mirror app._open_bookmark: a root that is itself a URL reopens verbatim;
        # host/path providers keep their in-source path suffix.
        if "://" in loc.root:
            return loc.root
        return loc.root + ("/" + "/".join(loc.parts) if loc.parts else "/")
