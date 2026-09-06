"""Exclusion masks for copy/move.

A user-supplied list of fnmatch patterns; any file **or directory** whose
*basename* matches one of them is left out of the transfer. Matching a
directory prunes its whole subtree — the point of the feature is to type
``node_modules;.git;*.pyc`` and have the copy skip those wholesale.

A pattern prefixed with ``-`` is an **exception**: an entry it matches is
kept even when another pattern would exclude it. That is what makes the
default ``.*;-.gitignore`` work — drop every dotfile, but carry
``.gitignore`` across. Exceptions always win over exclusions, regardless of
the order they were typed in. To exclude a file whose name really does start
with a dash, escape the first character as a class: ``[-]weird``.

Kept free of Textual/VFS imports so the file-op layer (``actions``,
``vfs_engine``) and the dialog can share it without an import cycle.

Matching is **case-sensitive** (``fnmatchcase``), deliberately: an
over-eager mask silently drops files the user asked to copy, which is worse
than a mask that matches nothing. Separators mirror
:func:`dunders.fm.find_file.parse_masks` — semicolon, comma or whitespace,
so ``*.pyc;*.log`` and ``*.pyc *.log`` both work.
"""

from __future__ import annotations

import fnmatch
import re


__all__ = ["DEFAULT_EXCLUDE", "parse_exclude_masks", "split_masks", "is_excluded"]


#: Prefilled into the copy/move dialog: skip dotfiles, but keep ``.gitignore``.
DEFAULT_EXCLUDE = ".*;-.gitignore"


def parse_exclude_masks(text: str) -> tuple[str, ...]:
    """Split user input into patterns; ``""`` -> ``()`` (exclude nothing).

    The ``-`` prefix is preserved here so the whole tuple — exclusions and
    exceptions together — is what gets threaded through the file-op layer;
    :func:`is_excluded` is the only place that has to know what it means.
    """
    if not text or not text.strip():
        return ()
    return tuple(chunk for chunk in re.split(r"[,;\s]+", text.strip()) if chunk)


def split_masks(masks: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split ``masks`` into ``(exclusions, exceptions)``, dropping the ``-``.

    A bare ``"-"`` carries no pattern and is dropped rather than turned into an
    exception that matches nothing.
    """
    drop: list[str] = []
    keep: list[str] = []
    for m in masks:
        if m.startswith("-"):
            if len(m) > 1:
                keep.append(m[1:])
        else:
            drop.append(m)
    return tuple(drop), tuple(keep)


def is_excluded(name: str, masks: tuple[str, ...]) -> bool:
    """Whether the entry basename ``name`` should be left out of the transfer.

    ``name`` is excluded when it matches an exclusion pattern and no ``-``
    exception. An empty mask tuple excludes nothing — that is the "feature off"
    state, so every caller can pass ``()`` unconditionally instead of branching.
    """
    if not masks:
        return False
    drop, keep = split_masks(masks)
    if not any(fnmatch.fnmatchcase(name, m) for m in drop):
        return False
    return not any(fnmatch.fnmatchcase(name, m) for m in keep)
