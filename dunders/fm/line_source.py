"""Random-access line sources shared by the lazy CSV and Markdown viewers.

A ``LineSource`` exposes lines 0..N without materialising them all. ``TextSource``
wraps an in-memory string; ``MmapSource`` mmaps a file and builds its newline
index incrementally so a multi-GB file opens instantly and never freezes the UI.
"""

from __future__ import annotations

import mmap
from array import array
from contextlib import suppress
from pathlib import Path

__all__ = ["LineSource", "TextSource", "MmapSource", "PREFIX_INDEX_LINES"]

# How many lines to index up front when opening an mmap source (first screen +
# a width sample); the rest is indexed on demand and in the background.
PREFIX_INDEX_LINES = 1024


class LineSource:
    """Random access to lines 0..N without materialising them all at once."""

    def line_count(self) -> int:
        raise NotImplementedError

    def line(self, i: int) -> str:
        raise NotImplementedError

    def sample(self) -> str:
        """First few KiB as text, for delimiter sniffing."""
        raise NotImplementedError

    def is_complete(self) -> bool:
        """True when ``line_count`` is final (in-memory sources always are)."""
        return True

    def index_batch(self, n: int) -> bool:
        """Index up to ``n`` more lines incrementally; return True while more
        remain. No-op for fully-known sources."""
        return False

    def close(self) -> None:
        pass


class TextSource(LineSource):
    """Lines from an in-memory string (small files, archive members)."""

    def __init__(self, text: str) -> None:
        self._lines = text.splitlines() or [""]

    def line_count(self) -> int:
        return len(self._lines)

    def line(self, i: int) -> str:
        return self._lines[i] if 0 <= i < len(self._lines) else ""

    def sample(self) -> str:
        return "\n".join(self._lines[:50])


class MmapSource(LineSource):
    """Lines from an mmap'd file via an *incremental* newline offset index.

    Opening indexes only a small prefix; the rest of the ``\\n`` index is built
    on demand (when a line is requested) and in the background (to grow the
    scrollbar), so even a multi-GB file opens instantly. Offsets live in a
    compact ``array('Q')``. Single-byte encodings only.
    """

    def __init__(self, path: Path) -> None:
        self._f = open(path, "rb")
        self._mm = mmap.mmap(self._f.fileno(), 0, access=mmap.ACCESS_READ)
        self._size = self._mm.size()
        self._starts = array("Q", [0])  # starts[i] = byte offset of line i
        self._scan_pos = 0
        self._eof = self._size == 0
        self.index_batch(PREFIX_INDEX_LINES)

    def _index_one(self) -> bool:
        if self._eof:
            return False
        nl = self._mm.find(b"\n", self._scan_pos)
        if nl == -1:
            self._eof = True
            return False
        self._scan_pos = nl + 1
        self._starts.append(self._scan_pos)
        return True

    def index_batch(self, n: int) -> bool:
        for _ in range(n):
            if not self._index_one():
                break
        return not self._eof

    def _index_to_line(self, i: int) -> None:
        while not self._eof and len(self._starts) < i + 2:
            self._index_one()

    def is_complete(self) -> bool:
        return self._eof

    def _exact_count(self) -> int:
        n = len(self._starts)
        # A trailing newline leaves a phantom empty start == size; drop it.
        if n > 1 and self._starts[-1] >= self._size:
            return n - 1
        return n

    def line_count(self) -> int:
        if self._eof:
            return self._exact_count()
        return max(0, len(self._starts) - 1)

    def line(self, i: int) -> str:
        if i < 0:
            return ""
        self._index_to_line(i)
        if i >= self.line_count():
            return ""
        begin = self._starts[i]
        end = self._starts[i + 1] if i + 1 < len(self._starts) else self._size
        return self._mm[begin:end].rstrip(b"\r\n").decode("utf-8", errors="replace")

    def sample(self) -> str:
        return self._mm[:8192].decode("utf-8", errors="replace")

    def close(self) -> None:
        with suppress(Exception):
            self._mm.close()
        with suppress(Exception):
            self._f.close()
