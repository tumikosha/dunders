"""transfer() — the single entry point for VFS copy/move.

The panel and the app go through :func:`transfer`, which resolves the providers
involved and dispatches:

* **Intra-provider** (source and dest share a scheme): delegate to the
  provider's ``copy_within`` / ``move_within`` fast path — byte-for-byte the old
  ``copy_paths`` / ``move_paths`` for ``file -> file``.
* **Cross-provider** (e.g. ``zip -> file`` extraction, ``file -> sftp`` upload):
  stream generically through the abstract provider contract —
  ``open_read`` on the source feeds ``open_write`` on the destination, recursing
  for directories. Neither provider knows about the other; the engine speaks
  only ``scan`` / ``is_dir`` / ``open_read`` / ``open_write`` / ``mkdir``.

This is the concrete payoff of "copy between any two sets of objects".
"""

from __future__ import annotations

import shutil
import threading
from typing import Literal

from dunders.core.vfs import VfsPath, VfsRegistry
from dunders.core.vfs.provider import ProgressCallback, VfsProvider
from dunders.fm.actions import OpError, OpResult


__all__ = ["transfer"]

TransferMode = Literal["copy", "move"]

_CHUNK = 1024 * 1024  # 1 MiB stream buffer


class _Cancelled(Exception):
    """Raised mid-walk when the cancel_event is set."""


def transfer(
    registry: VfsRegistry,
    sources: list[VfsPath],
    dest_dir: VfsPath,
    *,
    mode: TransferMode,
    rename_to: str | None = None,
    on_progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> OpResult:
    """Copy or move ``sources`` into ``dest_dir``.

    ``rename_to`` overrides the destination basename when there is exactly one
    source. Progress/cancellation semantics match ``dunders.fm.actions``.
    """
    if not sources:
        return OpResult()

    if all(s.scheme == dest_dir.scheme for s in sources):
        provider = registry.resolve(dest_dir)
        fast = provider.copy_within if mode == "copy" else provider.move_within
        result = fast(
            sources,
            dest_dir,
            rename_to=rename_to,
            on_progress=on_progress,
            cancel_event=cancel_event,
        )
        if result is not None:
            return result
        # Provider has no intra-scheme fast path -> fall through to streaming.

    return _generic_transfer(
        registry,
        sources,
        dest_dir,
        mode=mode,
        rename_to=rename_to,
        on_progress=on_progress,
        cancel_event=cancel_event,
    )


def _generic_transfer(
    registry: VfsRegistry,
    sources: list[VfsPath],
    dest_dir: VfsPath,
    *,
    mode: TransferMode,
    rename_to: str | None,
    on_progress: ProgressCallback | None,
    cancel_event: threading.Event | None,
) -> OpResult:
    result = OpResult()
    single_rename = rename_to if (rename_to and len(sources) == 1) else None

    total = max(sum(_count_files(registry, s) for s in sources), 1)
    counter = [0]

    def bump() -> None:
        counter[0] += 1
        if on_progress is not None:
            on_progress(counter[0], total)

    if on_progress is not None:
        on_progress(0, total)

    for src in sources:
        if _cancelled(cancel_event):
            result.cancelled = True
            return result
        dest = dest_dir.child(single_rename or src.name)
        try:
            _copy_tree(registry, src, dest, bump=bump, cancel_event=cancel_event)
        except _Cancelled:
            result.cancelled = True
            return result
        except OSError as exc:
            result.errors.append(OpError(loc=src, reason=str(exc)))
            continue
        result.succeeded.append(
            dest.to_local() if dest.scheme == "file" else dest
        )
        if mode == "move":
            # Source removal can be unsupported (read-only zip) — record it as
            # an error rather than crash the worker; the copy already landed.
            try:
                registry.resolve(src).delete([src], cancel_event=cancel_event)
            except OSError as exc:
                result.errors.append(
                    OpError(loc=src, reason=f"copied but not removed: {exc}")
                )
    return result


def _copy_tree(
    registry: VfsRegistry,
    src: VfsPath,
    dest: VfsPath,
    *,
    bump,
    cancel_event: threading.Event | None,
) -> None:
    if _cancelled(cancel_event):
        raise _Cancelled
    src_p = registry.resolve(src)
    dst_p = registry.resolve(dest)
    if src_p.is_dir(src):
        _ensure_dir(dst_p, dest)
        for child in src_p.scan(src, include_parent=False):
            _copy_tree(registry, child.loc, dest.child(child.name),
                       bump=bump, cancel_event=cancel_event)
    else:
        with src_p.open_read(src) as reader, dst_p.open_write(dest) as writer:
            shutil.copyfileobj(reader, writer, _CHUNK)
        bump()


def _ensure_dir(dst_p: VfsProvider, dest: VfsPath) -> None:
    """Create ``dest`` on the destination provider, tolerating pre-existence."""
    parent = dest.parent
    if parent is None:
        return
    # mkdir reports "already exists" via OpResult.errors (no raise); ignore it —
    # a genuinely unwritable dest surfaces when the first open_write fails.
    dst_p.mkdir(parent, dest.name)


def _count_files(registry: VfsRegistry, loc: VfsPath) -> int:
    provider = registry.resolve(loc)
    if not provider.is_dir(loc):
        return 1
    return sum(
        _count_files(registry, child.loc)
        for child in provider.scan(loc, include_parent=False)
    )


def _cancelled(event: threading.Event | None) -> bool:
    return event is not None and event.is_set()
