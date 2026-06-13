"""RowSource — the seam between a panel and its row data.

A ``FilePanel`` accesses its rows only by length, index, and iteration. By
routing that through a ``RowSource`` (a ``Sequence[FileEntry]``) instead of a
bare ``list``, the panel's rendering/cursor/selection code is decoupled from
*how* rows are stored:

* :class:`MaterializedRowSource` — holds the full listing in memory. Used by
  filesystem-like providers (``file``, ``zip``) where the whole directory is
  loadable and sorted/searched client-side. This is today's behaviour, verbatim.
* A future ``PagedRowSource`` — for huge/remote sources (a million-row DB table,
  an S3 bucket) — will keep only a windowed cache behind the same Sequence
  surface, fetching pages on demand and returning placeholder rows for offsets
  not yet loaded. The panel will not need to change to host it.

Cutting this seam now (while only the materialized case exists) is a pure,
behaviour-preserving refactor: ``MaterializedRowSource`` is a faithful list
wrapper, so every existing consumer — ``panel.entries[i]``, ``len(...)``,
``for e in panel.entries`` — keeps working unchanged.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from dunders.fm.file_entry import FileEntry


__all__ = ["RowSource", "MaterializedRowSource"]


class RowSource(Sequence[FileEntry]):
    """Indexed, length-aware, iterable access to a panel's rows.

    Marker base over :class:`collections.abc.Sequence`; concrete sources
    implement ``__len__`` and ``__getitem__``. The Sequence mixin supplies
    ``__iter__``, ``__contains__``, ``__reversed__``, ``index`` and ``count``.
    """


class MaterializedRowSource(RowSource):
    """A ``RowSource`` backed by a fully-loaded in-memory list."""

    def __init__(self, entries: Iterable[FileEntry] = ()) -> None:
        self._entries: list[FileEntry] = list(entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __getitem__(self, index):  # int -> FileEntry, slice -> list[FileEntry]
        return self._entries[index]
