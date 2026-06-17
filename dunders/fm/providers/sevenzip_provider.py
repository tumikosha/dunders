"""SevenZipProvider — browse a ``.7z`` archive via the external ``7z`` CLI.

Unlike :mod:`~dunders.fm.providers.zip_provider` (stdlib ``zipfile``), 7-Zip has
no Python stdlib support, so this provider shells out to a locally-installed
``7z`` binary:

- ``7z l -slt <archive>``      — technical listing → the directory-tree index.
- ``7z x -so <archive> <name>`` — stream one member's bytes to stdout.

Browse / view / extract-out and append (``7z a -si`` streams a new member);
member deletion is unsupported. Reads pull a member fully into memory — fine
for F3 viewing and extraction.

The binary is auto-detected (``7z`` / ``7zz`` / ``7za``); if none is present
the provider is simply not registered (see ``default_registry``), so the panel
never offers to enter a ``.7z`` it cannot open.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from dunders.core.vfs import VfsPath
from dunders.core.vfs.provider import ProgressCallback
from dunders.fm.actions import OpError, OpResult
from dunders.fm.file_entry import FileEntry


__all__ = ["SevenZipProvider", "find_7z"]

_BINARY_CANDIDATES = ("7z", "7zz", "7za")


def find_7z() -> str | None:
    """Path to a usable 7-Zip binary, or ``None`` if none is installed."""
    for name in _BINARY_CANDIDATES:
        path = shutil.which(name)
        if path:
            return path
    return None


@dataclass(frozen=True)
class _Node:
    name: str
    is_dir: bool
    size: int
    mtime: float


# parent-parts tuple -> list of immediate child nodes
_Index = dict[tuple[str, ...], list[_Node]]


def _parse_mtime(value: str) -> float:
    try:
        return time.mktime(time.strptime(value.strip(), "%Y-%m-%d %H:%M:%S"))
    except (ValueError, OverflowError):
        return 0.0


def _parse_listing(text: str) -> list[tuple[tuple[str, ...], bool, int, float]]:
    """Parse ``7z l -slt`` output into (parts, is_dir, size, mtime) records.

    The technical listing emits one ``Key = Value`` block per member after a
    ``----------`` separator; blocks are split by blank lines.
    """
    records: list[tuple[tuple[str, ...], bool, int, float]] = []
    in_members = False
    cur: dict[str, str] = {}

    def flush() -> None:
        path = cur.get("Path")
        if not path:
            return
        parts = tuple(p for p in path.replace("\\", "/").split("/") if p)
        if not parts:
            return
        is_dir = cur.get("Attributes", "").startswith("D")
        try:
            size = int(cur.get("Size", "") or 0)
        except ValueError:
            size = 0
        records.append((parts, is_dir, size, _parse_mtime(cur.get("Modified", ""))))

    for line in text.splitlines():
        if not in_members:
            if line.startswith("----------"):
                in_members = True
            continue
        if line.strip() == "":
            flush()
            cur = {}
            continue
        key, sep, val = line.partition(" = ")
        if sep:
            cur[key.strip()] = val
    flush()
    return records


def _build_index(records) -> _Index:
    grouped: dict[tuple[str, ...], dict[str, _Node]] = {}

    def ensure_dir(parts: tuple[str, ...]) -> None:
        for i in range(len(parts)):
            parent = parts[:i]
            name = parts[i]
            bucket = grouped.setdefault(parent, {})
            if name not in bucket:
                bucket[name] = _Node(name=name, is_dir=True, size=0, mtime=0.0)

    for parts, is_dir, size, mtime in records:
        ensure_dir(parts[:-1])
        if is_dir:
            ensure_dir(parts)
            continue
        grouped.setdefault(parts[:-1], {})[parts[-1]] = _Node(
            name=parts[-1], is_dir=False, size=size, mtime=mtime
        )

    return {parent: list(nodes.values()) for parent, nodes in grouped.items()}


class _SevenZipMemberWriter(io.RawIOBase):
    """A writable stream that pipes its bytes into ``7z a -si<name>``.

    The generic transfer engine does ``with open_write(dest) as w: copy into w``;
    this feeds the member to a ``7z a`` process via stdin and, on close, finishes
    the process and surfaces a non-zero exit as an OSError.
    """

    def __init__(self, binary: str, archive: str, inner: str) -> None:
        self._proc = subprocess.Popen(
            [binary, "a", archive, f"-si{inner}"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

    def writable(self) -> bool:
        return True

    def write(self, b) -> int:
        return self._proc.stdin.write(b)

    def close(self) -> None:
        if not self.closed and self._proc.stdin and not self._proc.stdin.closed:
            self._proc.stdin.close()
            rc = self._proc.wait()
            err = self._proc.stderr.read() if self._proc.stderr else b""
            if rc != 0:
                raise OSError(f"7z add failed: {err.decode(errors='replace').strip()}")
        super().close()


class SevenZipProvider:
    """Read-only ``VfsProvider`` for 7-Zip archives via the ``7z`` CLI."""

    scheme = "7z"
    display_name = "7-Zip archiver"
    capabilities = frozenset({"read", "stream", "write"})

    def __init__(self, binary: str | None = None) -> None:
        self._bin = binary or find_7z()
        # archive path -> ((mtime, size), index)
        self._cache: dict[str, tuple[tuple[float, int], _Index]] = {}

    # -- index cache ------------------------------------------------------

    def _require_bin(self) -> str:
        if self._bin is None:
            raise OSError("no 7z binary found on PATH")
        return self._bin

    def _index_for(self, loc: VfsPath) -> _Index:
        path = loc.root
        st = Path(path).stat()
        sig = (st.st_mtime, st.st_size)
        cached = self._cache.get(path)
        if cached is not None and cached[0] == sig:
            return cached[1]
        proc = subprocess.run(
            [self._require_bin(), "l", "-slt", path],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise OSError(f"7z failed to list {path!r}: {proc.stderr.strip()}")
        index = _build_index(_parse_listing(proc.stdout))
        self._cache[path] = (sig, index)
        return index

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
        parent = loc.parent
        if parent is None:
            parent = VfsPath.local(Path(loc.root).parent)
        return FileEntry(loc=parent, name="..", size=0, mtime=0.0, is_dir=True)

    def is_dir(self, loc: VfsPath) -> bool:
        if not loc.parts:
            return True
        index = self._index_for(loc)
        if loc.parts in index:
            return True
        parent = loc.parts[:-1]
        name = loc.parts[-1]
        return any(n.name == name and n.is_dir for n in index.get(parent, []))

    def resolve_target(
        self, spec: str, *, base: VfsPath, password: str | None = None
    ) -> VfsPath | None:
        """``7z:<name>`` → a locator for a ``.7z`` under ``base`` (create-or-open).

        An absent archive is materialised empty (``7z a`` with no files) so the
        panel can browse it immediately. ``base`` must be a local directory.
        """
        if base.scheme != "file":
            return None
        name = (spec or "").strip() or "archive.7z"
        if not name.lower().endswith(".7z"):
            name += ".7z"
        path = Path(name).expanduser()
        if not path.is_absolute():
            path = base.to_local() / path
        if not path.exists():
            # `7z a <archive>` packs the current directory, so run it from an
            # empty temp dir → no files gathered → a genuine empty archive.
            with tempfile.TemporaryDirectory() as empty:
                subprocess.run(
                    [self._require_bin(), "a", str(path)],
                    cwd=empty, capture_output=True,
                )
            if not path.exists():
                return None
        return VfsPath(scheme="7z", root=str(path), parts=())

    def open_read(self, loc: VfsPath) -> BinaryIO:
        inner = "/".join(loc.parts)
        proc = subprocess.run(
            [self._require_bin(), "x", "-so", loc.root, inner],
            capture_output=True,
        )
        if proc.returncode != 0:
            raise OSError(
                f"7z failed to read {inner!r}: {proc.stderr.decode(errors='replace').strip()}"
            )
        return io.BytesIO(proc.stdout)

    # -- write (append via `7z a -si`) ------------------------------------

    def _member_exists(self, loc: VfsPath) -> bool:
        try:
            index = self._index_for(loc)
        except OSError:
            return False  # archive not created yet -> nothing to clash with
        parent, name = loc.parts[:-1], loc.parts[-1]
        return any(n.name == name for n in index.get(parent, []))

    def open_write(
        self, loc: VfsPath, *, size_hint: int | None = None, overwrite: bool = False
    ) -> BinaryIO:
        """Stream a member into the archive via ``7z a -si<name>``.

        Default is append-only with the same policy as zip: an existing member
        name is refused. With ``overwrite=True`` (editing in place) the check is
        skipped — ``7z a`` replaces the member, which is exactly what we want.
        """
        inner = "/".join(loc.parts)
        if not inner:
            raise OSError("cannot write the archive root as a member")
        if not overwrite and self._member_exists(loc):
            raise FileExistsError(f"{inner} already exists in archive")
        return _SevenZipMemberWriter(self._require_bin(), loc.root, inner)

    def mkdir(self, parent: VfsPath, name: str) -> OpResult:
        # Directories are implied by member paths (``dir/file``); 7z has no
        # stream-add for an empty dir, so this is a no-op rather than an error,
        # letting the transfer engine recurse a tree. Empty dirs are not kept.
        return OpResult()

    def delete(
        self,
        targets: list[VfsPath],
        *,
        on_progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> OpResult:
        """Remove members via ``7z d`` (recursive for directory members)."""
        result = OpResult()
        inners = ["/".join(t.parts) for t in targets if t.parts]
        if not inners:
            return result
        proc = subprocess.run(
            [self._require_bin(), "d", targets[0].root, *inners],
            capture_output=True,
        )
        if proc.returncode != 0:
            result.errors.append(OpError(
                loc=targets[0],
                reason=proc.stderr.decode(errors="replace").strip() or "7z delete failed",
            ))
            return result
        if on_progress is not None:
            on_progress(len(inners), len(inners))
        return result

    def copy_within(self, sources, dest, *, rename_to=None, on_progress=None,
                    on_status=None, cancel_event=None) -> OpResult | None:
        return None  # extraction is cross-provider

    def move_within(self, sources, dest, *, rename_to=None, on_progress=None,
                    cancel_event=None) -> OpResult | None:
        return None
