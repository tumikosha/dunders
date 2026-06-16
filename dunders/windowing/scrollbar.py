"""Shared scrollbar look for the windowing layer.

``ThinScrollBarRender`` paints slim scrollbars instead of Textual's default
solid colour-filled bar (which reads as a thick block in a 1-cell gutter): a
half-width ``▌`` thumb on a thin ``▏`` track for vertical bars, and the
matching half-height ``▄`` thumb on a thin ``▁`` track for horizontal bars.

Apply per instance after a widget mounts::

    self.vertical_scrollbar.renderer = ThinScrollBarRender
    self.horizontal_scrollbar.renderer = ThinScrollBarRender
"""

from __future__ import annotations

from math import ceil

from rich.segment import Segment, Segments
from rich.style import Style as RichStyle

from textual.color import Color
from textual.scrollbar import ScrollBarRender


class ThinScrollBarRender(ScrollBarRender):
    @classmethod
    def render_bar(
        cls, size: int = 25, virtual_size: float = 50, window_size: float = 20,
        position: float = 0, thickness: int = 1, vertical: bool = True,
        back_color: Color = Color(85, 85, 85),
        bar_color: Color = Color(255, 0, 255),
    ) -> Segments:
        meta = {"@mouse.down": "grab"}
        # Textual passes rich colours at render time, but the defaults above are
        # textual Colors — normalise either to a rich colour.
        def _rc(c):
            return c.rich_color if hasattr(c, "rich_color") else c
        thumb = RichStyle(color=_rc(bar_color), meta=meta)
        # Track: dim default foreground (no explicit colour) — exactly the style
        # the tree's own scrollbar uses, so the two read identically.
        track = RichStyle(dim=True, meta=meta)
        # Full blocks fill the whole cell and read as "thick", so use half-cell
        # glyphs aligned to one edge: a half-width ▌ thumb / thin ▏ track for a
        # vertical bar, and the matching half-height ▄ thumb / ▁ track for a
        # horizontal one.
        thumb_glyph, track_glyph = ("▌", "▏") if vertical else ("▄", "▁")
        n = int(size)
        if window_size and size and virtual_size and size != virtual_size:
            thumb_size = max(1.0, window_size / (virtual_size / size))
            denom = (virtual_size - window_size) or 1
            start = int((size - thumb_size) * (position / denom))
            end = start + max(1, ceil(thumb_size))
            start = max(0, min(start, n))
            end = max(start + 1, min(end, n))
            glyphs = [thumb_glyph if start <= i < end else track_glyph for i in range(n)]
        else:
            glyphs = [track_glyph] * n
        segs = [Segment(g * thickness, thumb if g == thumb_glyph else track) for g in glyphs]
        if vertical:
            return Segments(segs, new_lines=True)
        return Segments((segs + [Segment.line()]) * thickness, new_lines=False)
