"""scan_folder_stats — recursively aggregate a directory's statistics.

Walks a :class:`~dunders.core.vfs.locator.VfsPath` directory through the generic
``VfsProvider.scan`` contract (so it works for local, sftp, zip, … alike),
totalling file count, sub-folder count, byte size, the largest file, and the
maximum nesting depth. Hidden entries are included, so the byte total is a true
``du``.

The walk is **cancellable** (checks a ``threading.Event`` per entry and returns
the partial result with ``partial=True`` rather than raising) and reports **live
progress** through an optional throttled callback — it is designed to run on a
background worker thread while a modal shows the counts and a Cancel button.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from typing import Callable

from dunders.core.vfs import VfsPath, VfsRegistry


__all__ = ["FolderStats", "scan_folder_stats"]

# How often (in entries processed) to fire the on_progress callback. Throttled so
# a huge tree doesn't flood call_from_thread; the final result is always emitted.
_PROGRESS_EVERY = 256


@dataclass
class FolderStats:
    """Aggregate statistics for a directory tree."""

    files: int = 0
    dirs: int = 0
    total_bytes: int = 0
    largest_name: str = ""
    largest_size: int = 0
    max_depth: int = 0
    partial: bool = False  # True if the scan was cancelled before completing


def scan_folder_stats(
    registry: VfsRegistry,
    loc: VfsPath,
    *,
    cancel_event: threading.Event | None = None,
    on_progress: Callable[[FolderStats], None] | None = None,
) -> FolderStats:
    """Recursively total the statistics under ``loc``.

    ``cancel_event`` — if set mid-walk, the walk stops and the partial result is
    returned with ``partial=True`` (the dialog shows what was gathered).
    ``on_progress`` — called with a *snapshot* of the running stats every
    ``_PROGRESS_EVERY`` entries and once at the end, for a live count-up.
    """
    stats = FolderStats()
    seen = [0]  # entries processed so far, for throttling on_progress

    def _cancelled() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    def _emit() -> None:
        if on_progress is not None:
            on_progress(replace(stats))  # snapshot: worker mutates, UI reads

    def _walk(node: VfsPath, depth: int) -> None:
        if _cancelled():
            stats.partial = True
            return
        try:
            provider = registry.resolve(node)
        except KeyError:
            return
        try:
            children = provider.scan(node, include_parent=False, show_hidden=True)
        except OSError:
            # Unreadable/vanished directory — skip it (best effort, like scan_dir).
            return
        for child in children:
            if _cancelled():
                stats.partial = True
                return
            if child.is_dir:
                stats.dirs += 1
                if depth + 1 > stats.max_depth:
                    stats.max_depth = depth + 1
                _walk(child.loc, depth + 1)
                if stats.partial:
                    return
            else:
                stats.files += 1
                size = max(child.size, 0)
                stats.total_bytes += size
                if size > stats.largest_size:
                    stats.largest_size = size
                    stats.largest_name = child.name
            seen[0] += 1
            if seen[0] % _PROGRESS_EVERY == 0:
                _emit()

    _walk(loc, 0)
    _emit()
    return stats
