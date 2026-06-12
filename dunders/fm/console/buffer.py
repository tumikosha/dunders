"""Bounded scrollback buffer of styled lines."""

from __future__ import annotations

from collections import deque
from typing import List

from .ansi import AnsiParser, Segment


class ConsoleBuffer:
    """Append-only line buffer with a fixed cap and a view-offset cursor."""

    def __init__(self, maxlen: int = 10_000) -> None:
        self._lines: deque[List[Segment]] = deque(maxlen=maxlen)
        self._partial_bytes: bytes = b""  # raw bytes of unterminated current line
        self._view_offset = 0  # 0 = bottom (auto-scroll); >0 = lines back

    # --- writes -----------------------------------------------------------

    def append_bytes(self, data: bytes) -> None:
        # Prepend any previously unterminated raw bytes so partial lines merge.
        combined = self._partial_bytes + data

        # Remove the previously displayed partial line from the deque
        # (it will be replaced by the re-parsed merged content).
        if self._partial_bytes:
            try:
                self._lines.pop()
            except IndexError:
                pass
            self._partial_bytes = b""

        # Use a fresh parser for each call — SGR state within a single
        # append_bytes call is fully self-contained for the buffer's purposes.
        parser = AnsiParser()
        yielded = list(parser.feed(combined))

        # feed() yields partial lines at end-of-buffer (no trailing \n).
        # It also yields an empty [] sentinel after a trailing \n.
        # We must distinguish "partial last line" from "newline-terminated".
        # Strategy: split on whether combined ends with \n (or \r\n).
        ends_with_newline = combined.endswith(b"\n")

        if ends_with_newline:
            # All yielded lines are complete; trailing [] sentinel is discarded.
            for line in yielded:
                if line:  # skip empty sentinel from trailing \n
                    self._lines.append(line)
                # preserve intentional blank lines (empty line content)
                elif line == [] and not combined.strip(b"\n\r"):
                    pass  # skip pure-whitespace-only input artefacts
        else:
            # Last item from feed() is the unterminated partial line.
            complete = yielded[:-1] if yielded else []
            partial = yielded[-1] if yielded else []
            for line in complete:
                if line:
                    self._lines.append(line)
            if partial:
                self._lines.append(partial)
            # Record raw bytes of the partial so next call can merge.
            # Find last \n to determine what's partial.
            last_nl = combined.rfind(b"\n")
            if last_nl == -1:
                self._partial_bytes = combined
            else:
                self._partial_bytes = combined[last_nl + 1 :]

    def clear(self) -> None:
        self._lines.clear()
        self._partial_bytes = b""
        self._view_offset = 0

    # --- reads ------------------------------------------------------------

    def line_count(self) -> int:
        return len(self._lines)

    def line(self, idx: int) -> List[Segment]:
        return self._lines[idx]

    def iter_lines(self, start: int, count: int) -> list[List[Segment]]:
        end = min(start + count, len(self._lines))
        return [self._lines[i] for i in range(max(0, start), end)]

    # --- scroll state -----------------------------------------------------

    @property
    def view_offset(self) -> int:
        return self._view_offset

    def scroll_up(self, by: int) -> None:
        self._view_offset = self._view_offset + by

    def scroll_down(self, by: int) -> None:
        self._view_offset = max(0, self._view_offset - by)

    def scroll_to_top(self) -> None:
        self._view_offset = max(0, len(self._lines) - 1)

    def scroll_to_bottom(self) -> None:
        self._view_offset = 0
