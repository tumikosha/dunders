"""Recursive file search ("Find file") — pure logic, no UI.

Mirrors the conventions of ``dunders.fm.actions``:

- Synchronous; the App layer wraps :func:`walk` in ``run_worker(thread=True)``.
- Errors during traversal (PermissionError, vanished entries, unreadable
  symlinks) are swallowed silently — best-effort partial output.
- Honours an optional ``cancel_event`` (``threading.Event``) so the user
  can interrupt a long search; on cancel, :func:`walk` returns
  ``FindResult(..., cancelled=True)`` with whatever was found so far.
- Reports progress via callbacks. ``on_progress`` is called **once per
  directory** (not per file) so :class:`Application.call_from_thread` is
  not flooded when scanning trees with millions of entries.
"""

from __future__ import annotations

import fnmatch
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


__all__ = [
    "FindOptions",
    "FindResult",
    "parse_masks",
    "walk",
]


# Read text files in 64 KiB chunks to keep memory bounded on huge files.
_TEXT_CHUNK = 64 * 1024
# Heuristic: if the first 8 KiB contains a NUL, treat the file as binary
# and skip it for content search. Matches the rule used by the hex viewer.
_BINARY_SNIFF_BYTES = 8 * 1024


@dataclass(frozen=True)
class FindOptions:
    """Search parameters captured from the Find file dialog.

    `masks` is a list of fnmatch-style patterns; a file matches the mask
    filter if it matches **any** of them. An empty list (no masks) is
    treated as "*", matching everything.

    `contains` is the substring the file must contain. Empty string
    disables the content filter (match files by name only).
    """

    masks: tuple[str, ...]
    case_sensitive_mask: bool
    contains: str
    case_sensitive_text: bool
    whole_words: bool
    search_for_folders: bool
    follow_symlinks: bool


@dataclass
class FindResult:
    matches: list[Path] = field(default_factory=list)
    files_scanned: int = 0
    folders_scanned: int = 0
    cancelled: bool = False


class _FindCancelled(Exception):
    """Raised inside the walk when ``cancel_event`` is set."""


def parse_masks(text: str) -> tuple[str, ...]:
    """Split user input into a tuple of fnmatch patterns.

    Accepts comma, semicolon, or whitespace as separators (Far Manager
    accepts the same). Empty pieces are dropped. ``""`` returns ``()``,
    which the walker interprets as "match everything".
    """
    if not text or not text.strip():
        return ()
    pieces: list[str] = []
    for chunk in re.split(r"[,;\s]+", text):
        chunk = chunk.strip()
        if chunk:
            pieces.append(chunk)
    return tuple(pieces)


def _name_matches(name: str, masks: tuple[str, ...], case_sensitive: bool) -> bool:
    if not masks:
        return True
    if case_sensitive:
        return any(fnmatch.fnmatchcase(name, m) for m in masks)
    name_l = name.lower()
    return any(fnmatch.fnmatchcase(name_l, m.lower()) for m in masks)


def _content_matches(
    path: Path,
    needle: str,
    *,
    case_sensitive: bool,
    whole_words: bool,
    cancel_event: threading.Event | None,
) -> bool:
    """Return True if `path` is a regular file containing `needle`.

    Decoded via latin-1 so every byte maps cleanly to a code point and the
    NUL-sniff heuristic decides binary vs text. Reads in 64 KiB chunks
    with a small overlap so substrings spanning a chunk boundary still
    match.
    """
    try:
        with path.open("rb") as fh:
            head = fh.read(_BINARY_SNIFF_BYTES)
            if b"\x00" in head:
                return False
            needle_bytes = needle.encode("latin-1", errors="replace")
            if whole_words:
                pattern = re.compile(
                    rb"\b" + re.escape(needle_bytes) + rb"\b",
                    flags=0 if case_sensitive else re.IGNORECASE,
                )
                if pattern.search(head):
                    return True
            else:
                hay = head if case_sensitive else head.lower()
                pin = needle_bytes if case_sensitive else needle_bytes.lower()
                if pin in hay:
                    return True

            overlap = max(len(needle_bytes) - 1, 0)
            tail = head[-overlap:] if overlap else b""
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise _FindCancelled
                chunk = fh.read(_TEXT_CHUNK)
                if not chunk:
                    return False
                buf = tail + chunk
                if whole_words:
                    if pattern.search(buf):
                        return True
                else:
                    hay = buf if case_sensitive else buf.lower()
                    pin = needle_bytes if case_sensitive else needle_bytes.lower()
                    if pin in hay:
                        return True
                tail = buf[-overlap:] if overlap else b""
    except OSError:
        return False


def _check_cancelled(event: threading.Event | None) -> None:
    if event is not None and event.is_set():
        raise _FindCancelled


def walk(
    root: Path,
    options: FindOptions,
    *,
    on_progress: Callable[[Path, int, int], None] | None = None,
    on_match: Callable[[Path], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> FindResult:
    """Recursively search ``root`` for entries matching ``options``.

    Parameters
    ----------
    on_progress:
        Called once per directory entered: ``(current_dir, files_scanned,
        folders_scanned)``. Throttled by the caller; the walker itself
        emits at most one progress event per directory.
    on_match:
        Called for each matching path as soon as it is identified.
        The same path also appears in :attr:`FindResult.matches`.
    cancel_event:
        If set during the walk, the walker stops and returns the
        partial result with ``cancelled=True``.

    Symlinks: a symlink to a directory is descended only when
    ``options.follow_symlinks`` is True; cycles are detected via the
    real-path of every visited directory.
    """
    result = FindResult()
    visited_realpaths: set[str] = set()

    def _emit_progress(d: Path) -> None:
        if on_progress is not None:
            on_progress(d, result.files_scanned, result.folders_scanned)

    def _emit_match(p: Path) -> None:
        result.matches.append(p)
        if on_match is not None:
            on_match(p)

    def _descend(directory: Path) -> None:
        _check_cancelled(cancel_event)
        result.folders_scanned += 1
        _emit_progress(directory)

        try:
            real = os.path.realpath(directory)
        except OSError:
            real = str(directory)
        if real in visited_realpaths:
            return
        visited_realpaths.add(real)

        try:
            it = os.scandir(directory)
        except OSError:
            return

        with it:
            for entry in it:
                _check_cancelled(cancel_event)
                try:
                    is_symlink = entry.is_symlink()
                    is_dir = entry.is_dir(follow_symlinks=options.follow_symlinks)
                except OSError:
                    continue

                child = Path(entry.path)
                name = entry.name

                if is_dir:
                    if options.search_for_folders and _name_matches(
                        name, options.masks, options.case_sensitive_mask
                    ):
                        # contains-text doesn't apply to folders.
                        if not options.contains:
                            _emit_match(child)
                    if is_symlink and not options.follow_symlinks:
                        continue
                    _descend(child)
                else:
                    result.files_scanned += 1
                    if not _name_matches(
                        name, options.masks, options.case_sensitive_mask
                    ):
                        continue
                    if options.contains:
                        if not _content_matches(
                            child,
                            options.contains,
                            case_sensitive=options.case_sensitive_text,
                            whole_words=options.whole_words,
                            cancel_event=cancel_event,
                        ):
                            continue
                    _emit_match(child)

    try:
        _descend(root)
    except _FindCancelled:
        result.cancelled = True
    return result
