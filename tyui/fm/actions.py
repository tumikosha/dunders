"""File operation helpers used by the FilePanel F5/F6/F7/F8 flows.

Each function returns an OpResult so the UI can render success/error
counts. Long operations honour an optional `cancel_event`
(`threading.Event`) and call `on_progress(index, total)` after each
processed entry so a ProgressDialog can update.

For directory operations (copy / delete) progress is per-FILE, not
per-top-level-path: a single source directory containing 1000 files
counts as 1000 progress steps, so the user sees the bar move and can
cancel mid-tree.

These helpers are deliberately synchronous — the App layer wraps them
with run_worker(thread=True) so the UI stays responsive on big trees.
"""

from __future__ import annotations

import os
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


__all__ = [
    "OpError",
    "OpResult",
    "copy_paths",
    "move_paths",
    "delete_paths",
    "mkdir_at",
]


@dataclass(frozen=True)
class OpError:
    path: Path
    reason: str


@dataclass
class OpResult:
    succeeded: list[Path] = field(default_factory=list)
    errors: list[OpError] = field(default_factory=list)
    cancelled: bool = False


ProgressCallback = Callable[[int, int], None]


class _Cancelled(Exception):
    """Raised inside a recursive walk when cancel_event is set."""


def _check_cancelled(event: threading.Event | None) -> bool:
    return event is not None and event.is_set()


def _count_entries(paths: list[Path]) -> int:
    """Approximate total work units = files + directories under `paths`."""
    n = 0
    for root in paths:
        try:
            if root.is_dir() and not root.is_symlink():
                for _ in os.walk(root):
                    pass  # noop iteration; we count via fast count below
                # Cheap count — re-walk and tally entries.
                for dirpath, dirnames, filenames in os.walk(root):
                    n += len(dirnames) + len(filenames)
                n += 1  # the root dir itself
            else:
                n += 1
        except OSError:
            n += 1
    return max(n, 1)


def mkdir_at(parent: Path, name: str) -> OpResult:
    """Create a directory inside `parent`. `name` may contain `/` for nesting."""
    target = parent / name
    result = OpResult()
    try:
        target.mkdir(parents=True, exist_ok=False)
    except OSError as e:
        result.errors.append(OpError(path=target, reason=str(e)))
        return result
    result.succeeded.append(target)
    return result


# --------------------------------------------------------------------------
# Copy
# --------------------------------------------------------------------------


def _copy_recursive(
    src: Path,
    dst: Path,
    bump: Callable[[], None],
    cancel_event: threading.Event | None,
) -> None:
    if _check_cancelled(cancel_event):
        raise _Cancelled
    if src.is_dir() and not src.is_symlink():
        dst.mkdir(parents=True, exist_ok=False)
        bump()
        for child in src.iterdir():
            _copy_recursive(child, dst / child.name, bump, cancel_event)
    else:
        # File or symlink — copy2 preserves metadata.
        shutil.copy2(src, dst, follow_symlinks=False)
        bump()


def copy_paths(
    paths: list[Path],
    dest_dir: Path,
    *,
    rename_to: str | None = None,
    on_progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> OpResult:
    """Copy each source path into `dest_dir`. Per-file progress.

    `rename_to` is honoured only when `paths` has exactly one entry — it
    overrides the destination basename so the user can copy-with-rename.
    """
    result = OpResult()
    total = _count_entries(paths)
    counter = [0]
    single_rename = rename_to if (rename_to and len(paths) == 1) else None

    def _bump() -> None:
        counter[0] += 1
        if on_progress is not None:
            on_progress(counter[0], total)

    if on_progress is not None:
        on_progress(0, total)

    for src in paths:
        if _check_cancelled(cancel_event):
            result.cancelled = True
            return result
        dest_name = single_rename or src.name
        target = dest_dir / dest_name
        try:
            if src.parent == dest_dir and dest_name == src.name:
                raise OSError("source and destination are the same directory")
            _copy_recursive(src, target, _bump, cancel_event)
        except _Cancelled:
            result.cancelled = True
            return result
        except OSError as e:
            result.errors.append(OpError(path=src, reason=str(e)))
            continue
        result.succeeded.append(target)
    return result


# --------------------------------------------------------------------------
# Move
# --------------------------------------------------------------------------


def move_paths(
    paths: list[Path],
    dest_dir: Path,
    *,
    rename_to: str | None = None,
    on_progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> OpResult:
    """Move each source path into `dest_dir` via shutil.move.

    Move is atomic per-source-path on the same filesystem (rename), so
    progress is also per-source-path. Cross-filesystem moves degrade to
    copy+delete and may take longer; cancel granularity is still
    per-source-path.

    `rename_to` is honoured only when `paths` has exactly one entry — it
    overrides the destination basename so the user can move-with-rename.
    """
    result = OpResult()
    total = max(len(paths), 1)
    single_rename = rename_to if (rename_to and len(paths) == 1) else None
    if on_progress is not None:
        on_progress(0, total)

    for i, src in enumerate(paths):
        if _check_cancelled(cancel_event):
            result.cancelled = True
            return result
        dest_name = single_rename or src.name
        dst_path = dest_dir / dest_name
        try:
            if src.parent == dest_dir and dest_name == src.name:
                raise OSError("source and destination are the same directory")
            shutil.move(str(src), str(dst_path))
        except OSError as e:
            result.errors.append(OpError(path=src, reason=str(e)))
            if on_progress is not None:
                on_progress(i + 1, total)
            continue
        result.succeeded.append(dst_path)
        if on_progress is not None:
            on_progress(i + 1, total)
    return result


# --------------------------------------------------------------------------
# Delete
# --------------------------------------------------------------------------


def _delete_recursive(
    path: Path,
    bump: Callable[[], None],
    cancel_event: threading.Event | None,
) -> None:
    if _check_cancelled(cancel_event):
        raise _Cancelled
    if path.is_dir() and not path.is_symlink():
        for child in list(path.iterdir()):
            _delete_recursive(child, bump, cancel_event)
        path.rmdir()
        bump()
    else:
        os.unlink(path)
        bump()


def delete_paths(
    paths: list[Path],
    *,
    on_progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> OpResult:
    """Delete each path. Per-file progress for directories."""
    result = OpResult()
    total = _count_entries(paths)
    counter = [0]

    def _bump() -> None:
        counter[0] += 1
        if on_progress is not None:
            on_progress(counter[0], total)

    if on_progress is not None:
        on_progress(0, total)

    for p in paths:
        if _check_cancelled(cancel_event):
            result.cancelled = True
            return result
        try:
            _delete_recursive(p, _bump, cancel_event)
        except _Cancelled:
            result.cancelled = True
            return result
        except OSError as e:
            result.errors.append(OpError(path=p, reason=str(e)))
            continue
        result.succeeded.append(p)
    return result
