"""Minimal ANSI parser.

Supports:
- SGR (\\e[...m): reset, bold, dim, italic, underline, fg/bg 8/16, bright,
  256-color (38;5;n / 48;5;n), truecolor (38;2;r;g;b / 48;2;r;g;b).
- Control chars: \\r (column reset), \\b (backspace), \\t (8-space tabs), \\n.

Swallowed (no error): all other CSI sequences (\\e[...A/B/H/J/...), OSC
(\\e]...\\x07 or \\e]...\\e\\\\), DCS, etc.

The parser is chunk-safe: feeding bytes split across an escape produces
correct output once the rest arrives.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

_MAX_PENDING = 4096

_BASIC_FG = {
    30: "black", 31: "red", 32: "green", 33: "yellow",
    34: "blue", 35: "magenta", 36: "cyan", 37: "white",
    90: "bright_black", 91: "bright_red", 92: "bright_green",
    93: "bright_yellow", 94: "bright_blue", 95: "bright_magenta",
    96: "bright_cyan", 97: "bright_white",
}
_BASIC_BG = {k + 10: v for k, v in _BASIC_FG.items()}


@dataclass(frozen=True, slots=True)
class Style:
    fg: str | None = None
    bg: str | None = None
    bold: bool = False
    dim: bool = False
    italic: bool = False
    underline: bool = False


@dataclass(frozen=True, slots=True)
class Segment:
    text: str
    style: Style = field(default_factory=Style)


def _apply_sgr(style: Style, params: list[int]) -> Style:
    if not params:
        params = [0]
    i = 0
    fg = style.fg
    bg = style.bg
    bold = style.bold
    dim = style.dim
    italic = style.italic
    underline = style.underline
    while i < len(params):
        p = params[i]
        if p == 0:
            fg = bg = None
            bold = dim = italic = underline = False
        elif p == 1:
            bold = True
        elif p == 2:
            dim = True
        elif p == 3:
            italic = True
        elif p == 4:
            underline = True
        elif p == 22:
            bold = dim = False
        elif p == 23:
            italic = False
        elif p == 24:
            underline = False
        elif p == 39:
            fg = None
        elif p == 49:
            bg = None
        elif p in _BASIC_FG:
            fg = _BASIC_FG[p]
        elif p in _BASIC_BG:
            bg = _BASIC_BG[p]
        elif p in (38, 48) and i + 1 < len(params):
            mode = params[i + 1]
            if mode == 5 and i + 2 < len(params):
                color = f"color({params[i + 2]})"
                if p == 38:
                    fg = color
                else:
                    bg = color
                i += 2
            elif mode == 2 and i + 4 < len(params):
                r, g, b = params[i + 2], params[i + 3], params[i + 4]
                color = f"rgb({r},{g},{b})"
                if p == 38:
                    fg = color
                else:
                    bg = color
                i += 4
        i += 1
    return Style(fg=fg, bg=bg, bold=bold, dim=dim, italic=italic, underline=underline)


class AnsiParser:
    """Stateful, chunk-safe ANSI/control-char decoder."""

    def __init__(self) -> None:
        self._style = Style()
        # Each line is represented as a flat char buffer (list of (char, Style))
        # for positional overstrike support, then compacted to Segments on flush.
        self._line: list[tuple[str, Style]] = []  # (char, style) per column
        self._col = 0                              # current write column
        self._pending = b""                        # bytes from a partial escape
        self._cr_done = False                      # True after \r until next write
        self._after_newline = False                # True immediately after \n

    # --- public API -------------------------------------------------------

    def feed(self, data: bytes) -> Iterator[list[Segment]]:
        buf = self._pending + data
        self._pending = b""
        i = 0
        n = len(buf)
        while i < n:
            ch = buf[i]
            if ch == 0x1B:  # ESC
                end = self._scan_escape(buf, i)
                if end == -1:
                    # Incomplete: stash and wait for more.
                    self._pending = buf[i:]
                    if len(self._pending) > _MAX_PENDING:
                        self._pending = b""  # abandon malformed escape, recover on next byte
                    # Yield buffered content so far (without flushing the line)
                    # so split-chunk tests can see characters written before ESC.
                    # We emit a snapshot of current segments without resetting state.
                    partial = _compact(self._line)
                    if partial:
                        yield partial
                        self._line = []
                        self._col = 0
                    return
                self._handle_escape(buf[i:end])
                i = end
                continue
            if ch == 0x0A:  # \n
                yield self._flush_line()
                self._after_newline = True
                i += 1
                continue
            if ch == 0x0D:  # \r
                self._col = 0
                self._cr_done = True
                i += 1
                continue
            if ch == 0x08:  # \b
                if self._col > 0:
                    self._col -= 1
                    # Remove the character at current col (shrink line)
                    if self._col < len(self._line):
                        self._line.pop(self._col)
                i += 1
                continue
            if ch == 0x09:  # \t
                spaces = 8 - (self._col % 8)
                self._write_chars(" " * spaces)
                i += 1
                continue
            if ch < 0x20:
                # other control chars: drop
                i += 1
                continue
            # consume a UTF-8 codepoint
            if ch < 0x80:
                char_len = 1
            elif ch < 0xC0:
                # stray continuation byte: drop
                i += 1
                continue
            elif ch < 0xE0:
                char_len = 2
            elif ch < 0xF0:
                char_len = 3
            else:
                char_len = 4
            if i + char_len > n:
                self._pending = buf[i:]
                return
            s = buf[i : i + char_len].decode("utf-8", errors="replace")
            self._write_chars(s)
            i += char_len
        # End of buffer: yield anything buffered (unterminated line)
        if self._line:
            yield _compact(self._line)
            self._line = []
            self._col = 0
        elif self._after_newline:
            # A trailing \n left an empty pending line — emit it.
            yield []
        self._after_newline = False

    def flush_pending(self) -> list[Segment]:
        """Yield whatever is buffered as a final unterminated line."""
        return self._flush_line()

    # --- internal ---------------------------------------------------------

    def _write_chars(self, text: str) -> None:
        """Write text at current column, overstriking or appending."""
        self._after_newline = False
        if self._cr_done:
            # After \r: truncate line to col (which is 0) then append.
            self._line = self._line[: self._col]
            self._cr_done = False
        for ch in text:
            if self._col < len(self._line):
                self._line[self._col] = (ch, self._style)
            else:
                # Extend with spaces if col > len (shouldn't happen normally)
                while len(self._line) < self._col:
                    self._line.append((" ", self._style))
                self._line.append((ch, self._style))
            self._col += 1

    def _scan_escape(self, buf: bytes, start: int) -> int:
        """Return end-index past the escape, or -1 if incomplete."""
        n = len(buf)
        if start + 1 >= n:
            return -1
        kind = buf[start + 1]
        if kind == ord("["):  # CSI
            i = start + 2
            while i < n and buf[i] < 0x40:  # parameter/intermediate bytes
                i += 1
            if i >= n:
                return -1
            return i + 1
        if kind == ord("]"):  # OSC, terminated by BEL or ST (\e\\)
            i = start + 2
            while i < n:
                if buf[i] == 0x07:
                    return i + 1
                if buf[i] == 0x1B and i + 1 < n and buf[i + 1] == ord("\\"):
                    return i + 2
                i += 1
            return -1
        # 2-byte escapes (ESC =, ESC >, ESC c, ...) — eat one byte after ESC
        return start + 2

    def _handle_escape(self, esc: bytes) -> None:
        if len(esc) >= 3 and esc[1] == ord("[") and esc[-1] == ord("m"):
            params_str = esc[2:-1].decode("ascii", errors="ignore")
            try:
                params = [int(p) if p else 0 for p in params_str.split(";")]
            except ValueError:
                return
            self._style = _apply_sgr(self._style, params)
        # All other escapes silently dropped.

    def _flush_line(self) -> list[Segment]:
        out = _compact(self._line)
        self._line = []
        self._col = 0
        self._cr_done = False
        self._after_newline = False
        return out


def _compact(line: list[tuple[str, Style]]) -> list[Segment]:
    """Collapse a per-character list into runs of identical style."""
    if not line:
        return []
    segments: list[Segment] = []
    run_text: list[str] = []
    run_style = line[0][1]
    for ch, sty in line:
        if sty == run_style:
            run_text.append(ch)
        else:
            if run_text:
                segments.append(Segment("".join(run_text), run_style))
            run_text = [ch]
            run_style = sty
    if run_text:
        segments.append(Segment("".join(run_text), run_style))
    return segments
