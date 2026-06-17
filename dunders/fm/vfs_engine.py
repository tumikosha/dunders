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

import threading
from typing import Literal

from dunders.core.vfs import VfsPath, VfsRegistry
from dunders.core.vfs.provider import ProgressCallback, VfsProvider
from dunders.fm.actions import CopyStatus, OpError, OpResult, StatusCallback


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
    on_status: StatusCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> OpResult:
    """Copy or move ``sources`` into ``dest_dir``.

    ``rename_to`` overrides the destination basename when there is exactly one
    source. Progress/cancellation semantics match ``dunders.fm.actions``.

    ``on_status`` is the rich copy channel (current file + byte progress); the
    UI wires it up so a big single file animates the bar. ``on_progress`` is
    the legacy whole-item counter still used by move.
    """
    if not sources:
        return OpResult()

    if all(s.scheme == dest_dir.scheme for s in sources):
        provider = registry.resolve(dest_dir)
        if mode == "copy":
            result = provider.copy_within(
                sources,
                dest_dir,
                rename_to=rename_to,
                on_progress=on_progress,
                on_status=on_status,
                cancel_event=cancel_event,
            )
        else:
            result = provider.move_within(
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
        on_status=on_status,
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
    on_status: StatusCallback | None,
    cancel_event: threading.Event | None,
) -> OpResult:
    result = OpResult()
    single_rename = rename_to if (rename_to and len(sources) == 1) else None

    # Measure once so the bar has a denominator. When sizes are available
    # (local/zip/sftp listings carry st_size) we drive the bar by BYTES so a
    # single huge file animates and stays cancellable; otherwise we fall back
    # to a whole-file counter.
    total_files = 0
    total_bytes = 0
    for s in sources:
        f, b = _measure(registry, s)
        total_files += f
        total_bytes += b
    total_files = max(total_files, 1)
    use_bytes = total_bytes > 0

    done_files = [0]
    done_bytes = [0]

    def on_chunk(label: str, n: int) -> None:
        done_bytes[0] += n
        if use_bytes and on_status is not None:
            on_status(CopyStatus(done_bytes[0], total_bytes, label, is_bytes=True))

    def on_file_done(label: str) -> None:
        done_files[0] += 1
        if on_progress is not None:
            on_progress(done_files[0], total_files)
        if not use_bytes and on_status is not None:
            on_status(CopyStatus(done_files[0], total_files, label, is_bytes=False))

    if on_progress is not None:
        on_progress(0, total_files)
    if on_status is not None:
        if use_bytes:
            on_status(CopyStatus(0, total_bytes, "", is_bytes=True))
        else:
            on_status(CopyStatus(0, total_files, "", is_bytes=False))

    for src in sources:
        if _cancelled(cancel_event):
            result.cancelled = True
            return result
        dest = dest_dir.child(single_rename or src.name)
        try:
            _copy_tree(registry, src, dest, on_chunk=on_chunk,
                       on_file_done=on_file_done, cancel_event=cancel_event)
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
    on_chunk,
    on_file_done,
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
                       on_chunk=on_chunk, on_file_done=on_file_done,
                       cancel_event=cancel_event)
    else:
        label = src.name
        try:
            with src_p.open_read(src) as reader, dst_p.open_write(dest) as writer:
                while True:
                    if _cancelled(cancel_event):
                        raise _Cancelled
                    chunk = reader.read(_CHUNK)
                    if not chunk:
                        break
                    writer.write(chunk)
                    on_chunk(label, len(chunk))
        except _Cancelled:
            _cleanup_partial(dst_p, dest)
            raise
        on_file_done(label)


def _ensure_dir(dst_p: VfsProvider, dest: VfsPath) -> None:
    """Create ``dest`` on the destination provider, tolerating pre-existence."""
    parent = dest.parent
    if parent is None:
        return
    # mkdir reports "already exists" via OpResult.errors (no raise); ignore it —
    # a genuinely unwritable dest surfaces when the first open_write fails.
    dst_p.mkdir(parent, dest.name)


def _measure(registry: VfsRegistry, loc: VfsPath) -> tuple[int, int]:
    """``(file_count, total_bytes)`` under ``loc`` from directory listings.

    Sizes come from the ``scan`` entries (``st_size``) — no file is opened —
    so a tree is measured with one listdir per directory, the same round trips
    the copy itself makes. ``total_bytes`` is 0 when a provider doesn't report
    sizes; the caller then drives the bar by file count instead.
    """
    provider = registry.resolve(loc)
    if not provider.is_dir(loc):
        return 1, _size_of(provider, loc)
    files = total = 0
    try:
        children = provider.scan(loc, include_parent=False)
    except OSError:
        return 1, 0
    for child in children:
        if child.is_dir:
            f, b = _measure(registry, child.loc)
            files += f
            total += b
        else:
            files += 1
            total += max(child.size, 0)
    return files, total


def _size_of(provider: VfsProvider, loc: VfsPath) -> int:
    """Size of a single file ``loc`` via its parent listing (best effort)."""
    parent = loc.parent
    if parent is None:
        return 0
    try:
        for entry in provider.scan(parent, include_parent=False):
            if entry.name == loc.name:
                return max(entry.size, 0)
    except OSError:
        pass
    return 0


def _cleanup_partial(dst_p: VfsProvider, dest: VfsPath) -> None:
    """Remove a half-written destination after a mid-file cancel (best effort)."""
    try:
        dst_p.delete([dest])
    except Exception:
        pass


def _cancelled(event: threading.Event | None) -> bool:
    return event is not None and event.is_set()
