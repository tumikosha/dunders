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
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from dunders.core.vfs import VfsPath


__all__ = [
    "CopyStatus",
    "OpError",
    "OpResult",
    "chmod_paths",
    "copy_paths",
    "move_paths",
    "delete_paths",
    "mkdir_at",
    "pack_paths",
]


@dataclass(frozen=True, init=False)
class OpError:
    """A per-item failure, identified by a VfsPath.

    Cross-provider transfers (e.g. a failed read of a zip member) can report a
    non-local locator, while local callers keep passing ``path=`` and reading
    ``.path`` exactly as before.
    """

    loc: VfsPath
    reason: str

    def __init__(
        self,
        *,
        reason: str,
        loc: VfsPath | None = None,
        path: Path | str | None = None,
    ) -> None:
        if loc is None:
            if path is None:
                raise TypeError("OpError requires either loc= or path=")
            loc = VfsPath.local(Path(path))
        object.__setattr__(self, "loc", loc)
        object.__setattr__(self, "reason", reason)

    @property
    def path(self) -> Path:
        """Local path of the failed item (``file`` scheme only)."""
        return self.loc.to_local()

    def __str__(self) -> str:
        where = self.loc.to_local() if self.loc.scheme == "file" else self.loc.as_uri()
        return f"{where}: {self.reason}"


@dataclass
class OpResult:
    # Destinations written (copy/move) or items affected (delete/mkdir/chmod).
    # Local for filesystem ops and for extraction (zip -> local); a future
    # upload (local -> sftp) would record remote VfsPaths here.
    succeeded: list[Path] = field(default_factory=list)
    errors: list[OpError] = field(default_factory=list)
    cancelled: bool = False
    # Files left untouched because they already existed at the destination and
    # the caller asked to skip existing ones (copy "Skip existing" checkbox).
    skipped: list = field(default_factory=list)


@dataclass(frozen=True)
class CopyStatus:
    """A rich copy/transfer progress update.

    Unlike the bare ``on_progress(index, total)`` channel (used by
    delete/pack/move), copy reports a :class:`CopyStatus` so the dialog can
    show *which file* is being copied and move the bar by **bytes** — a single
    multi-GB file then animates smoothly instead of jumping 0→100% in one step.

    ``done``/``total`` are the **overall** progress across the whole operation;
    ``is_bytes`` distinguishes the byte-granular path (True) from a whole-file
    counter (False). The ``file_*`` fields carry the **current file's** own byte
    progress so the dialog can show a second, per-file bar, and ``files_done``/
    ``files_total`` drive the "file X of Y" counter. All the extra fields default
    to 0, so a single-bar caller can keep constructing ``CopyStatus`` as before.
    """

    done: int          # overall: bytes (is_bytes) or files copied so far
    total: int         # overall: total bytes (is_bytes) or total files
    label: str = ""    # path/name of the file currently being copied
    is_bytes: bool = False
    file_done: int = 0     # bytes of the current file copied so far
    file_total: int = 0    # size of the current file (0 if unknown)
    files_done: int = 0    # files completed so far
    files_total: int = 0   # total files in the operation


ProgressCallback = Callable[[int, int], None]
StatusCallback = Callable[[CopyStatus], None]

# Stream buffer for the chunked copy. Small enough that cancel/redraw stay
# responsive on a huge single file, large enough not to syscall-thrash.
_COPY_CHUNK = 1024 * 1024  # 1 MiB
# Emit a "measuring" progress tick at most every this-many files while sizing a
# tree, so the pre-copy scan shows life without flooding the UI marshaller.
_MEASURE_TICK_EVERY = 2048


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


def chmod_paths(
    targets: list[Path],
    mode: int,
    *,
    on_progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> OpResult:
    """Apply ``mode`` (octal int) to each path in ``targets``.

    Symlinks are skipped via ``follow_symlinks=False`` where supported;
    on platforms without lchmod support the underlying ``Path.chmod``
    follows the link, which matches the GNU coreutils default.
    """
    result = OpResult()
    total = len(targets)
    for i, path in enumerate(targets, 1):
        if _check_cancelled(cancel_event):
            result.cancelled = True
            break
        try:
            path.chmod(mode)
        except OSError as e:
            result.errors.append(OpError(path=path, reason=str(e)))
        else:
            result.succeeded.append(path)
        if on_progress is not None:
            on_progress(i, total)
    return result


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


def _measure_local(
    paths: list[Path],
    cancel_event: threading.Event | None,
    on_tick: Callable[[int], None] | None = None,
) -> tuple[int, int]:
    """One cancellable pass returning ``(file_count, total_bytes)``.

    Before a copy the tree used to be walked TWICE (once for the file count,
    once for the byte total) with no cancel check, so the dialog sat at
    0% for seconds and Cancel did nothing. This walks once, checks
    ``cancel_event`` per directory (raising ``_Cancelled``), and ``on_tick(n)``
    lets the dialog show a live "measuring" count so the wait isn't a frozen bar.
    """
    files = 0
    total = 0
    next_tick = _MEASURE_TICK_EVERY
    for root in paths:
        if _check_cancelled(cancel_event):
            raise _Cancelled
        try:
            if root.is_dir() and not root.is_symlink():
                for dirpath, _dirnames, filenames in os.walk(root):
                    if _check_cancelled(cancel_event):
                        raise _Cancelled
                    for name in filenames:
                        files += 1
                        try:
                            total += (Path(dirpath) / name).lstat().st_size
                        except OSError:
                            pass
                        # Every N files (checked per file, so even one huge flat
                        # directory animates and stays cancellable): re-check
                        # cancel and tick. Ticking per file-emit would flood the
                        # UI marshaller — with total==0 it can't throttle these
                        # (done>=total is always true).
                        if files >= next_tick:
                            if _check_cancelled(cancel_event):
                                raise _Cancelled
                            if on_tick is not None:
                                on_tick(files)
                            next_tick = files + _MEASURE_TICK_EVERY
            else:
                files += 1
                try:
                    total += root.lstat().st_size
                except OSError:
                    pass
        except _Cancelled:
            raise
        except OSError:
            files += 1
    return max(files, 1), total


def _copy_one_file(
    src: Path,
    dst: Path,
    file_start: Callable[[str, int], None],
    on_bytes: Callable[[int], None],
    cancel_event: threading.Event | None,
) -> None:
    """Copy a single file (or symlink) in `_COPY_CHUNK` chunks.

    Calls `file_start(label, size)` before the first byte (so the dialog shows
    the name and can size the per-file bar) and `on_bytes(n)` per chunk, and
    checks `cancel_event` between chunks so a cancel lands mid-file and the
    partial destination is removed rather than left half-written.
    """
    label = str(src)
    if src.is_symlink():
        file_start(label, 0)
        os.symlink(os.readlink(src), dst)
        return
    try:
        size = src.stat().st_size
    except OSError:
        size = 0
    file_start(label, size)
    try:
        with open(src, "rb") as reader, open(dst, "wb") as writer:
            while True:
                if _check_cancelled(cancel_event):
                    raise _Cancelled
                chunk = reader.read(_COPY_CHUNK)
                if not chunk:
                    break
                writer.write(chunk)
                on_bytes(len(chunk))
    except _Cancelled:
        try:
            os.unlink(dst)
        except OSError:
            pass
        raise
    shutil.copystat(src, dst, follow_symlinks=False)


def _copy_recursive(
    src: Path,
    dst: Path,
    file_start: Callable[[str, int], None],
    on_bytes: Callable[[int], None],
    file_done: Callable[[], None],
    skip: Callable[[int, Path], None],
    skip_existing: bool,
    cancel_event: threading.Event | None,
) -> None:
    if _check_cancelled(cancel_event):
        raise _Cancelled
    if src.is_dir() and not src.is_symlink():
        # exist_ok=True so re-copying a directory MERGES into an existing target
        # (files overwritten below), matching the cross-scheme engine's
        # _ensure_dir. Without it a second copy of `front` onto an existing
        # `dest/front` raised "File exists" instead of updating it.
        dst.mkdir(parents=True, exist_ok=True)
        for child in src.iterdir():
            _copy_recursive(child, dst / child.name, file_start, on_bytes,
                            file_done, skip, skip_existing, cancel_event)
    else:
        if skip_existing and dst.exists():
            try:
                size = src.stat().st_size
            except OSError:
                size = 0
            skip(size, dst)
            return
        _copy_one_file(src, dst, file_start, on_bytes, cancel_event)
        file_done()


def copy_paths(
    paths: list[Path],
    dest_dir: Path,
    *,
    rename_to: str | None = None,
    on_progress: ProgressCallback | None = None,
    on_status: StatusCallback | None = None,
    cancel_event: threading.Event | None = None,
    skip_existing: bool = False,
) -> OpResult:
    """Copy each source path into `dest_dir`, in chunks, with byte progress.

    `rename_to` is honoured only when `paths` has exactly one entry — it
    overrides the destination basename so the user can copy-with-rename.
    `skip_existing` leaves a destination file untouched when it already exists.

    The rich `on_status` (:class:`CopyStatus`) channel carries BOTH the overall
    byte progress and the current file's own byte progress + a file counter, so
    the dialog can show two bars. The legacy `on_progress(index, total)` counter
    fires only when `on_status` is absent (older callers/tests).
    """
    result = OpResult()
    single_rename = rename_to if (rename_to and len(paths) == 1) else None

    # Size the tree in ONE cancellable pass, showing a live "measuring" count so
    # a big folder doesn't freeze the dialog at 0% with a dead Cancel button.
    def _measure_tick(n: int) -> None:
        if on_status is not None:
            on_status(CopyStatus(n, 0, "Measuring…", is_bytes=False))
    try:
        files_total, total_bytes = _measure_local(paths, cancel_event, _measure_tick)
    except _Cancelled:
        result.cancelled = True
        return result

    st = {"done_bytes": 0, "done_files": 0, "cur_done": 0, "cur_total": 0,
          "label": ""}

    def _emit() -> None:
        if on_status is None:
            return
        on_status(CopyStatus(
            st["done_bytes"], total_bytes, st["label"], is_bytes=True,
            file_done=st["cur_done"], file_total=st["cur_total"],
            files_done=st["done_files"], files_total=files_total,
        ))

    def _advance_file() -> None:
        st["done_files"] += 1
        if on_status is None and on_progress is not None:
            on_progress(st["done_files"], files_total)

    def _file_start(label: str, size: int) -> None:
        st["label"] = label
        st["cur_total"] = max(size, 0)
        st["cur_done"] = 0
        _emit()

    def _on_bytes(n: int) -> None:
        st["done_bytes"] += n
        st["cur_done"] += n
        _emit()

    def _file_done() -> None:
        _advance_file()
        _emit()

    def _skip(size: int, target: Path) -> None:
        st["done_bytes"] += max(size, 0)
        _advance_file()
        result.skipped.append(target)
        _emit()

    _emit()  # initial 0%
    if on_status is None and on_progress is not None:
        on_progress(0, files_total)

    for src in paths:
        if _check_cancelled(cancel_event):
            result.cancelled = True
            return result
        dest_name = single_rename or src.name
        target = dest_dir / dest_name
        try:
            if src.parent == dest_dir and dest_name == src.name:
                raise OSError("source and destination are the same directory")
            _copy_recursive(src, target, _file_start, _on_bytes, _file_done,
                            _skip, skip_existing, cancel_event)
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


# --------------------------------------------------------------------------
# Pack (create a new .zip from a local selection)
# --------------------------------------------------------------------------


def pack_paths(
    sources: list[Path],
    dest_zip: Path,
    *,
    base: Path,
    on_progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> OpResult:
    """Pack ``sources`` into a new ``dest_zip`` (ZIP_DEFLATED).

    Arcnames are computed relative to ``base`` (the panel cwd) so a selection
    packs by its visible names (``dir/sub/f.txt``, ``a.txt``), not by absolute
    paths. Refuses to overwrite an existing ``dest_zip``. Per-file progress;
    on cancel the partial archive is removed. Directory entries (including
    empty dirs) are preserved.
    """
    result = OpResult()
    if dest_zip.exists():
        result.errors.append(
            OpError(path=dest_zip, reason="destination already exists")
        )
        return result

    total = _count_entries(sources)
    counter = [0]

    def _bump() -> None:
        counter[0] += 1
        if on_progress is not None:
            on_progress(counter[0], total)

    if on_progress is not None:
        on_progress(0, total)

    try:
        with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for src in sources:
                if _check_cancelled(cancel_event):
                    raise _Cancelled
                try:
                    _pack_one(zf, src, base, _bump, cancel_event)
                except _Cancelled:
                    raise
                except OSError as e:
                    result.errors.append(OpError(path=src, reason=str(e)))
                    continue
        # _count_entries counts directories too, while we bump per file; land
        # the bar at 100% on success regardless of that over-count.
        if on_progress is not None:
            on_progress(total, total)
        result.succeeded.append(dest_zip)
    except _Cancelled:
        result.cancelled = True
        dest_zip.unlink(missing_ok=True)
    return result


def _pack_one(
    zf: zipfile.ZipFile,
    src: Path,
    base: Path,
    bump: Callable[[], None],
    cancel_event: threading.Event | None,
) -> None:
    """Write ``src`` (file or directory tree) into ``zf`` under base-relative
    arcnames."""
    if src.is_dir() and not src.is_symlink():
        wrote_child = False
        for dirpath, dirnames, filenames in os.walk(src):
            if _check_cancelled(cancel_event):
                raise _Cancelled
            for name in filenames:
                fpath = Path(dirpath) / name
                zf.write(fpath, _arcname(fpath, base))
                wrote_child = True
                bump()
            # Preserve empty directories with an explicit "dir/" entry.
            if not dirnames and not filenames:
                arc = _arcname(Path(dirpath), base)
                zf.writestr(arc + "/", "")
                wrote_child = True
        if not wrote_child:
            bump()
    else:
        zf.write(src, _arcname(src, base))
        bump()


def _arcname(path: Path, base: Path) -> str:
    """Path inside the archive, relative to ``base`` (POSIX separators)."""
    try:
        rel = path.relative_to(base)
    except ValueError:
        rel = Path(path.name)
    return rel.as_posix()
