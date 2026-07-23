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


def _resolve_or_none(registry: VfsRegistry, loc: VfsPath) -> VfsProvider | None:
    """Provider for ``loc``'s scheme, or None if none is registered."""
    try:
        return registry.resolve(loc)
    except KeyError:
        return None


def _is_slow_dir(registry: VfsRegistry, loc: VfsPath) -> bool:
    """True if ``loc`` is a directory on a slow (network) provider — the case
    whose recursive pre-measure is expensive enough to skip (see the lazy path
    in ``_generic_transfer``). A slow *file* is cheap to stat, so returns False."""
    provider = _resolve_or_none(registry, loc)
    if provider is None or "slow" not in getattr(provider, "capabilities", frozenset()):
        return False
    try:
        return provider.is_dir(loc)
    except OSError:
        return False


def _dest_exists(provider: VfsProvider, loc: VfsPath) -> bool:
    """True if ``loc`` already exists on ``provider`` (file or dir).

    Uses the provider's own ``exists`` when available (cheap: one stat), else
    falls back to ``is_dir`` plus a parent-listing scan — enough for the
    "Skip existing" copy option on any scheme."""
    fn = getattr(provider, "exists", None)
    if callable(fn):
        try:
            return bool(fn(loc))
        except OSError:
            return False
    try:
        if provider.is_dir(loc):
            return True
    except OSError:
        pass
    parent = loc.parent
    if parent is None:
        return False
    try:
        return any(
            e.name == loc.name
            for e in provider.scan(parent, include_parent=False, show_hidden=True)
        )
    except OSError:
        return False


class _CopyProgress:
    """Two-channel copy progress: overall (bytes across the whole operation)
    plus the current file's own byte progress and a file counter. Every mutation
    emits a :class:`CopyStatus` carrying both bars; the UI throttles the flood.

    ``use_bytes`` drives the overall bar by bytes (sizes were measured);
    ``indeterminate`` is the lazy sftp-directory case (unknown total → count-up).
    Otherwise the overall bar counts whole files.
    """

    def __init__(self, *, on_status, on_progress, total_bytes, total_files,
                 use_bytes, indeterminate):
        self.on_status = on_status
        self.on_progress = on_progress
        self.total_bytes = total_bytes
        self.total_files = total_files
        self.use_bytes = use_bytes
        self.indeterminate = indeterminate
        self.done_bytes = 0
        self.done_files = 0
        self.cur_label = ""
        self.cur_done = 0
        self.cur_total = 0

    def emit(self) -> None:
        if self.on_status is None:
            return
        if self.use_bytes:
            d, t, ib = self.done_bytes, self.total_bytes, True
        elif self.indeterminate:
            d, t, ib = self.done_bytes, 0, True
        else:
            d, t, ib = self.done_files, self.total_files, False
        self.on_status(CopyStatus(
            d, t, self.cur_label, ib,
            file_done=self.cur_done, file_total=self.cur_total,
            files_done=self.done_files,
            files_total=0 if self.indeterminate else self.total_files,
        ))

    def file_start(self, name: str, size: int) -> None:
        self.cur_label = name
        self.cur_total = max(size, 0)
        self.cur_done = 0
        self.emit()

    def chunk(self, n: int) -> None:
        self.done_bytes += n
        self.cur_done += n
        self.emit()

    def _advance_file(self) -> None:
        self.done_files += 1
        # Legacy whole-file counter: only when nobody consumes the rich status
        # (older callers/tests). With on_status wired the dialog reads the file
        # counter straight off CopyStatus, so firing on_progress too would fight
        # the two-bar display by flipping it into set_progress mode.
        if (self.on_progress is not None and self.on_status is None
                and not self.indeterminate):
            self.on_progress(self.done_files, self.total_files)

    def file_done(self) -> None:
        self._advance_file()
        self.emit()

    def skip(self, size: int) -> None:
        # A skipped file was counted in the measured total, so advance the
        # overall bar by its size (and the file counter) to keep 100% reachable.
        self.done_bytes += max(size, 0)
        self._advance_file()
        self.emit()


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
    skip_existing: bool = False,
) -> OpResult:
    """Copy or move ``sources`` into ``dest_dir``.

    ``rename_to`` overrides the destination basename when there is exactly one
    source. Progress/cancellation semantics match ``dunders.fm.actions``.

    ``on_status`` is the rich copy channel (current file + byte progress); the
    UI wires it up so a big single file animates the bar. ``on_progress`` is
    the legacy whole-item counter still used by move. ``skip_existing`` leaves a
    destination file untouched when it already exists (per-file).
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
                skip_existing=skip_existing,
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
        skip_existing=skip_existing,
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
    skip_existing: bool = False,
) -> OpResult:
    result = OpResult()
    single_rename = rename_to if (rename_to and len(sources) == 1) else None

    # Measure once so the bar has a denominator. When sizes are available
    # (local/zip listings carry st_size) we drive the bar by BYTES so a single
    # huge file animates and stays cancellable; otherwise we fall back to a
    # whole-file counter.
    #
    # BUT a slow (network) *directory* is measured LAZILY: pre-walking a deep
    # sftp tree is one round trip per directory under a serialized connection
    # lock — for a large tree (node_modules, …) that is a long, formerly
    # uninterruptible phase that pinned the bar at 0% and doubled the round trips
    # (measure, then copy). For those we skip the pre-measure and drive an
    # indeterminate count-up bar from the copy walk itself, so the transfer
    # starts at once and Cancel responds. A slow *file* is still measured — that
    # is a single cheap stat and keeps the byte bar exact for a big download.
    lazy = any(_is_slow_dir(registry, s) for s in sources)
    total_files = 0
    total_bytes = 0
    if not lazy:
        try:
            for s in sources:
                f, b = _measure(registry, s, cancel_event=cancel_event)
                total_files += f
                total_bytes += b
        except _Cancelled:
            result.cancelled = True
            return result
    total_files = max(total_files, 1)
    prog = _CopyProgress(
        on_status=on_status, on_progress=on_progress,
        total_bytes=total_bytes, total_files=total_files,
        use_bytes=total_bytes > 0, indeterminate=lazy,
    )
    prog.emit()  # initial 0%
    if on_progress is not None and on_status is None and not lazy:
        on_progress(0, total_files)

    for src in sources:
        if _cancelled(cancel_event):
            result.cancelled = True
            return result
        dest = dest_dir.child(single_rename or src.name)
        try:
            src_provider = registry.resolve(src)
            export = getattr(src_provider, "export_as_file", None)
            exported = export(src) if callable(export) else None
            if exported is not None:
                name, reader = exported
                out_name = single_rename or name
                # Exports carry a fixed format suffix (e.g. a DB table -> .jsonl).
                # The copy dialog pre-fills the source's dir-name, which for a
                # table has no extension, so single_rename would otherwise drop
                # the suffix and land the file as bare "<table>".
                _dot = name.rfind(".")
                suffix = name[_dot:] if _dot > 0 else ""
                if suffix and not out_name.endswith(suffix):
                    out_name += suffix
                dst = dest_dir.child(out_name)
                dst_p = registry.resolve(dst)
                if skip_existing and _dest_exists(dst_p, dst):
                    reader.close()
                    prog.skip(0)
                    result.skipped.append(dst.to_local() if dst.scheme == "file" else dst)
                    continue
                prog.file_start(name, 0)
                try:
                    with reader, dst_p.open_write(dst) as writer:
                        owns = _attach_writer_progress(
                            writer, lambda _l, n: prog.chunk(n), name)
                        while True:
                            if _cancelled(cancel_event):
                                raise _Cancelled
                            chunk = reader.read(_CHUNK)
                            if not chunk:
                                break
                            writer.write(chunk)
                            if not owns:
                                prog.chunk(len(chunk))
                except _Cancelled:
                    _cleanup_partial(dst_p, dst)
                    raise
                prog.file_done()
                result.succeeded.append(dst.to_local() if dst.scheme == "file" else dst)
                continue  # exported sources are copy-only even in move mode (data-safe; the table is not deleted)
            _copy_tree(registry, src, dest, size=None, skip_existing=skip_existing,
                       prog=prog, result=result, cancel_event=cancel_event)
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
    size: int | None,
    skip_existing: bool,
    prog: "_CopyProgress",
    result: OpResult,
    cancel_event: threading.Event | None,
) -> None:
    if _cancelled(cancel_event):
        raise _Cancelled
    src_p = registry.resolve(src)
    dst_p = registry.resolve(dest)
    if src_p.is_dir(src):
        _ensure_dir(dst_p, dest)
        # A copy is view-agnostic: walk the FULL tree, hidden entries included.
        # The panel's show_hidden is a display filter — a directory copy that
        # honoured it would silently drop dotfiles (.git, .env, …). Most visible
        # on cross-scheme transfers (sftp/zip) that route through this engine;
        # local→local goes through actions.copy_paths and was never affected.
        # child.size is carried straight into the recursion so per-file progress
        # needs no extra stat round trip.
        for child in src_p.scan(src, include_parent=False, show_hidden=True):
            _copy_tree(registry, child.loc, dest.child(child.name),
                       size=child.size, skip_existing=skip_existing,
                       prog=prog, result=result, cancel_event=cancel_event)
    else:
        fsize = size if size is not None else _size_of(src_p, src)
        if skip_existing and _dest_exists(dst_p, dest):
            prog.skip(fsize)
            result.skipped.append(dest.to_local() if dest.scheme == "file" else dest)
            return
        prog.file_start(src.name, fsize)
        try:
            with src_p.open_read(src) as reader, dst_p.open_write(dest) as writer:
                owns = _attach_writer_progress(
                    writer, lambda _l, n: prog.chunk(n), src.name)
                while True:
                    if _cancelled(cancel_event):
                        raise _Cancelled
                    chunk = reader.read(_CHUNK)
                    if not chunk:
                        break
                    writer.write(chunk)
                    if not owns:
                        prog.chunk(len(chunk))
        except _Cancelled:
            _cleanup_partial(dst_p, dest)
            raise
        prog.file_done()


def _ensure_dir(dst_p: VfsProvider, dest: VfsPath) -> None:
    """Create ``dest`` on the destination provider, tolerating pre-existence."""
    parent = dest.parent
    if parent is None:
        return
    # mkdir reports "already exists" via OpResult.errors (no raise); ignore it —
    # a genuinely unwritable dest surfaces when the first open_write fails.
    dst_p.mkdir(parent, dest.name)


def _measure(
    registry: VfsRegistry,
    loc: VfsPath,
    *,
    cancel_event: threading.Event | None = None,
) -> tuple[int, int]:
    """``(file_count, total_bytes)`` under ``loc`` from directory listings.

    Sizes come from the ``scan`` entries (``st_size``) — no file is opened —
    so a tree is measured with one listdir per directory, the same round trips
    the copy itself makes. ``total_bytes`` is 0 when a provider doesn't report
    sizes; the caller then drives the bar by file count instead.

    Raises ``_Cancelled`` if ``cancel_event`` fires: on a deep tree over a slow
    (network) provider this walk can take a long time, and it must stay
    interruptible so the Cancel button works during the measure phase.
    """
    if _cancelled(cancel_event):
        raise _Cancelled
    provider = registry.resolve(loc)
    if not provider.is_dir(loc):
        return 1, _size_of(provider, loc)
    # A "directory" that exports as a single file (e.g. a DB table -> .jsonl) is
    # copied as ONE file, not walked as a tree. Recursing it would re-page the
    # whole table just to size it and stall the bar at 0%; use the provider's
    # cheap size hint instead (None -> not export-capable -> measure normally).
    hint = getattr(provider, "export_size_hint", None)
    if callable(hint):
        try:
            est = hint(loc)
        except OSError:
            est = None
        if est is not None:
            return 1, est
    files = total = 0
    try:
        # Match _copy_tree: measure the full tree (dotfiles included) so the
        # progress bar's denominator equals what actually gets copied.
        children = provider.scan(loc, include_parent=False, show_hidden=True)
    except OSError:
        return 1, 0
    for child in children:
        if _cancelled(cancel_event):
            raise _Cancelled
        if child.is_dir:
            f, b = _measure(registry, child.loc, cancel_event=cancel_event)
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
        # show_hidden so a dotfile's size resolves too (else it reads back 0).
        for entry in provider.scan(parent, include_parent=False, show_hidden=True):
            if entry.name == loc.name:
                return max(entry.size, 0)
    except OSError:
        pass
    return 0


def _attach_writer_progress(writer, on_chunk, label: str) -> bool:
    """Let a writer that does its heavy work at ``close`` drive the byte bar.

    Some destinations don't compress in ``write`` — notably 7z, which buffers
    stdin and only then runs LZMA. Feeding such a writer makes the byte counter
    race to 100% before the real work starts, then freeze during ``close``. If
    the writer exposes ``attach_progress``, hand it a sink that advances the bar
    by real byte deltas (the writer reports them from the tool's own progress)
    and return True so the copy loop stops counting fed bytes itself."""
    attach = getattr(writer, "attach_progress", None)
    if attach is None:
        return False
    return bool(attach(lambda n: on_chunk(label, n)))


def _cleanup_partial(dst_p: VfsProvider, dest: VfsPath) -> None:
    """Remove a half-written destination after a mid-file cancel (best effort)."""
    try:
        dst_p.delete([dest])
    except Exception:
        pass


def _cancelled(event: threading.Event | None) -> bool:
    return event is not None and event.is_set()
