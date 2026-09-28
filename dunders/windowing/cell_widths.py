"""Align Rich's character-width table with what the terminal really draws.

Rich (and so Textual) sizes every glyph from the latest Unicode tables, but
terminals disagree on emoji. macOS Terminal.app draws ``⭐``/``⚡`` — BMP
emoji that only became wide in Unicode 9 — in ONE cell, and ``🗣`` — a
text-presentation pictograph Rich calls narrow — in TWO. Each mismatch shifts
the rest of the row: the window's right border moved left on every Markdown
table row starting with ``⭐``.

The fix is the same idea as vim's ``setcellwidths()``: at startup, before the
app takes the screen, print a few representative glyphs, ask the terminal
where the cursor ended up (``ESC[6n``) and, per group of glyphs that share
the behaviour, override Rich's width table when every representative agrees.
All Rich/Textual width math goes through ``rich.cells.load_cell_table``, so a
patched table fixes rendering app-wide.

Limitation: the method trusts ``ESC[6n``. PyCharm's JediTerm answers it from
its buffer, where a non-BMP character (UTF-16 surrogate pair, e.g. ``🔌``)
takes 2 columns, but paints that character in 1 cell — rows containing one
still shift left there, and no table value can fix it (see CLAUDE.md).

Pure parts (`groups`, `plan_overrides`, `patched_table`) are unit-tested; the
terminal I/O in `measure` is best effort — no answer within the timeout means
no change.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from functools import cache

log = logging.getLogger(__name__)

VS16 = "️"


@dataclass(frozen=True)
class ProbeGroup:
    """Glyphs expected to share one width behaviour, plus the few of them the
    terminal is actually asked about."""

    name: str
    members: frozenset[str]
    probes: tuple[str, ...]
    # One member followed by VS16, to learn whether VS16 widens the group.
    vs16_probe: str


def _width_in(table, codepoint: int) -> int:
    lo, hi = 0, len(table.widths) - 1
    while lo <= hi:
        mid = (lo + hi) >> 1
        start, end, width = table.widths[mid]
        if codepoint < start:
            hi = mid - 1
        elif codepoint > end:
            lo = mid + 1
        else:
            return width
    return 1


@cache
def groups() -> tuple[ProbeGroup, ...]:
    from rich._unicode_data import load

    latest = load("latest")
    unicode8 = load("8.0.0")
    # BMP glyphs wide in current Unicode but narrow in 8.0: ⭐ ⚡ ⌚ ✅ …
    # Terminals on older tables draw them in one cell.
    bmp_emoji = frozenset(
        chr(cp) for cp in range(0x2000, 0x10000)
        if _width_in(latest, cp) == 2 and _width_in(unicode8, cp) == 1
    )
    # SMP pictographs with text presentation (narrow in Unicode, widened by
    # VS16): 🗣 🌡 🖥 … Many terminals draw every SMP pictograph wide.
    smp_text = frozenset(
        ch for ch in latest.narrow_to_wide
        if 0x1F000 <= ord(ch) <= 0x1FAFF and _width_in(latest, ord(ch)) == 1
    )
    return (
        ProbeGroup("bmp_emoji", bmp_emoji, ("⭐", "⚡", "⌚"), "⚡" + VS16),
        ProbeGroup("smp_text", smp_text, ("🗣", "🌡", "🖥"), "🗣" + VS16),
    )


@dataclass
class Overrides:
    widths: dict[int, int] = field(default_factory=dict)
    vs16_add: set[str] = field(default_factory=set)
    vs16_remove: set[str] = field(default_factory=set)

    def __bool__(self) -> bool:
        return bool(self.widths or self.vs16_add or self.vs16_remove)


def plan_overrides(
    measured: dict[str, int | None], rich_width, probe_groups=None,
) -> Overrides:
    """Decide the table changes from terminal measurements.

    ``measured`` maps each probe string to the cells the terminal advanced
    (None when unknown); ``rich_width`` is Rich's own measure for a string.
    A group is overridden only when every probe was measured, all agree on a
    width, and that width differs from Rich's — a partial or noisy answer
    changes nothing.
    """
    out = Overrides()
    for group in probe_groups if probe_groups is not None else groups():
        widths = {measured.get(p) for p in group.probes}
        if None in widths or len(widths) != 1:
            continue
        term = widths.pop()
        if term not in (1, 2):
            continue
        rich = {rich_width(p) for p in group.probes}
        changed = rich != {term}
        if changed:
            out.widths.update((ord(ch), term) for ch in group.members)
        # VS16: Rich adds one cell to a member of ``narrow_to_wide``. Match
        # whatever the terminal does with it after this group's glyphs.
        vs16 = measured.get(group.vs16_probe)
        if vs16 is None:
            continue
        if vs16 == term + 1:
            out.vs16_add.update(group.members)
        elif vs16 == term:
            out.vs16_remove.update(group.members)
    return out


def patched_table(table, overrides: Overrides):
    """A copy of a Rich ``CellTable`` with ``overrides`` applied."""
    ranges: list[tuple[int, int, int]] = []
    points = sorted(overrides.widths)
    for start, end, width in table.widths:
        # Cut every overridden code point out of this range.
        lo = start
        for cp in points:
            if cp < lo or cp > end:
                continue
            if cp > lo:
                ranges.append((lo, cp - 1, width))
            lo = cp + 1
        if lo <= end:
            ranges.append((lo, end, width))
    for cp, width in overrides.widths.items():
        if width != 1:  # absent from the table means one cell
            ranges.append((cp, cp, width))
    ranges.sort()
    narrow_to_wide = (
        set(table.narrow_to_wide) | overrides.vs16_add
    ) - overrides.vs16_remove
    return table._replace(widths=ranges, narrow_to_wide=frozenset(narrow_to_wide))


def apply(overrides: Overrides) -> None:
    """Install ``overrides`` into Rich's width math for the default version."""
    import rich.cells as cells

    original = cells.load_cell_table
    patched = patched_table(original("auto"), overrides)

    def load_cell_table(unicode_version: str = "auto"):
        if unicode_version == "auto":
            return patched
        return original(unicode_version)

    cells.load_cell_table = load_cell_table
    cells.get_character_cell_size.cache_clear()
    cells.cached_cell_len.cache_clear()


def measure(probes: list[str], timeout: float = 0.5) -> dict[str, int | None]:
    """Ask the terminal how many cells each probe advances the cursor.

    Writes all probes with a cursor-position request after each, then reads
    the replies; everything is erased from the line afterwards. Returns an
    empty dict when not on a POSIX tty or when the terminal stays silent.
    """
    try:
        import select
        import termios
        import tty
    except ImportError:
        return {}
    fd_in, fd_out = sys.stdin.fileno(), sys.stdout.fileno()
    if not (os.isatty(fd_in) and os.isatty(fd_out)):
        return {}
    old = termios.tcgetattr(fd_in)
    replies = b""
    try:
        tty.setraw(fd_in)
        request = b"".join(b"\r" + p.encode() + b"\x1b[6n" for p in probes)
        os.write(fd_out, request + b"\r\x1b[2K")
        want = len(probes)
        deadline = time.monotonic() + timeout
        while replies.count(b"R") < want:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            ready, _, _ = select.select([fd_in], [], [], left)
            if not ready:
                break
            replies += os.read(fd_in, 1024)
    except OSError:
        log.debug("cell width probe failed", exc_info=True)
        return {}
    finally:
        termios.tcsetattr(fd_in, termios.TCSADRAIN, old)
    cols = [int(m.group(1)) for m in re.finditer(rb"\x1b\[\d+;(\d+)R", replies)]
    if len(cols) != len(probes):
        return {}
    # Each probe starts at column 1 (after "\r"), so width = column - 1.
    return {p: col - 1 for p, col in zip(probes, cols)}


def calibrate() -> Overrides:
    """Measure the terminal and patch Rich's widths to match. Best effort.

    Disabled with ``DUNDERS_CELL_PROBE=0``.
    """
    if os.environ.get("DUNDERS_CELL_PROBE", "1") == "0":
        return Overrides()
    from rich.cells import cell_len

    probe_list: list[str] = []
    for group in groups():
        probe_list.extend(group.probes)
        probe_list.append(group.vs16_probe)
    try:
        measured = measure(probe_list)
    except Exception:
        log.debug("cell width probe crashed", exc_info=True)
        return Overrides()
    if not measured:
        return Overrides()
    overrides = plan_overrides(measured, cell_len)
    if overrides:
        apply(overrides)
    return overrides
