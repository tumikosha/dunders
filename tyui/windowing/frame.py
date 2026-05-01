"""Pure rendering primitives for window frames: borders, titles, decorations.

This module contains no Textual widgets — just dataclasses describing frame
parameters and static functions that turn them into strings. Keeping it pure
makes it trivial to unit-test every border style × sides × decoration
combination without booting a Textual app.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

Side = Literal["top", "right", "bottom", "left"]


class BorderStyle(StrEnum):
    NONE = "none"
    SINGLE = "single"
    DOUBLE = "double"
    ROUNDED = "rounded"
    HEAVY = "heavy"
    DASHED = "dashed"
    ASCII = "ascii"


# (top-left, top, top-right, right, bottom-right, bottom, bottom-left, left, horizontal-dashed, vertical-dashed)
# For non-dashed styles the last two entries equal plain horizontal/vertical.
_BORDER_CHARS: dict[BorderStyle, dict[str, str]] = {
    BorderStyle.SINGLE: {
        "tl": "┌", "t": "─", "tr": "┐",
        "l": "│", "r": "│",
        "bl": "└", "b": "─", "br": "┘",
    },
    BorderStyle.DOUBLE: {
        "tl": "╔", "t": "═", "tr": "╗",
        "l": "║", "r": "║",
        "bl": "╚", "b": "═", "br": "╝",
    },
    BorderStyle.ROUNDED: {
        "tl": "╭", "t": "─", "tr": "╮",
        "l": "│", "r": "│",
        "bl": "╰", "b": "─", "br": "╯",
    },
    BorderStyle.HEAVY: {
        "tl": "┏", "t": "━", "tr": "┓",
        "l": "┃", "r": "┃",
        "bl": "┗", "b": "━", "br": "┛",
    },
    BorderStyle.DASHED: {
        "tl": "┌", "t": "╌", "tr": "┐",
        "l": "╎", "r": "╎",
        "bl": "└", "b": "╌", "br": "┘",
    },
    BorderStyle.ASCII: {
        "tl": "+", "t": "-", "tr": "+",
        "l": "|", "r": "|",
        "bl": "+", "b": "-", "br": "+",
    },
    BorderStyle.NONE: {
        "tl": " ", "t": " ", "tr": " ",
        "l": " ", "r": " ",
        "bl": " ", "b": " ", "br": " ",
    },
}


@dataclass(frozen=True)
class BorderSides:
    top: bool = True
    right: bool = True
    bottom: bool = True
    left: bool = True

    @classmethod
    def all(cls) -> "BorderSides":
        return cls(True, True, True, True)

    @classmethod
    def none(cls) -> "BorderSides":
        return cls(False, False, False, False)

    @classmethod
    def only(cls, *sides: Side) -> "BorderSides":
        return cls(
            top="top" in sides,
            right="right" in sides,
            bottom="bottom" in sides,
            left="left" in sides,
        )

    def has_any(self) -> bool:
        return self.top or self.right or self.bottom or self.left


@dataclass
class Decorations:
    close_box: bool = False
    zoom_box: bool = False
    resize_grip: bool = False
    number: int | None = None
    subtitle: str | None = None


@dataclass
class TitleSpec:
    text: str = ""
    align: Literal["left", "center", "right"] = "left"
    padding: int = 1
    role: str = "window.title"


# --- Rendering --------------------------------------------------------------


def _chars(style: BorderStyle) -> dict[str, str]:
    return _BORDER_CHARS[style]


def _truncate(text: str, max_width: int) -> str:
    if max_width <= 0:
        return ""
    if len(text) <= max_width:
        return text
    if max_width == 1:
        return "…"
    return text[: max_width - 1] + "…"


def render_top(
    width: int,
    style: BorderStyle,
    sides: BorderSides,
    title: TitleSpec,
    decorations: Decorations,
) -> str:
    """Top row of the frame (corners + horizontal edge + title + decorations).

    Returns a raw string of length `width`. If ``sides.top`` is False, returns
    an empty string — the caller must treat this row as content instead.
    """
    if not sides.top or width <= 0:
        return ""

    chars = _chars(style)
    # Left corner exists only if both top and left sides are enabled.
    left_corner = chars["tl"] if sides.left else chars["t"]
    right_corner = chars["tr"] if sides.right else chars["t"]
    fill = chars["t"]

    # Reserve space for corners.
    inner_width = width - (1 if sides.left else 0) - (1 if sides.right else 0)
    if inner_width < 0:
        return (left_corner + right_corner)[:width]

    # Build decoration prefix (close_box + number) and suffix (zoom_box).
    prefix_parts: list[str] = []
    if decorations.close_box:
        prefix_parts.append(f"[■]")
    if decorations.number is not None:
        prefix_parts.append(f"─ {decorations.number} ─")
    prefix = "".join(prefix_parts)

    suffix = "[↕]" if decorations.zoom_box else ""

    # Title + padding.
    title_text = ""
    if title.text:
        pad = " " * title.padding
        title_text = f"{pad}{title.text}{pad}"

    # Compose inner line according to alignment.
    inner = _compose_top_inner(
        inner_width=inner_width,
        fill=fill,
        prefix=prefix,
        suffix=suffix,
        title=title_text,
        align=title.align,
    )

    result = f"{left_corner if sides.left else ''}{inner}{right_corner if sides.right else ''}"
    # Ensure exact width (pad or truncate).
    if len(result) < width:
        result += fill * (width - len(result))
    return result[:width]


def _compose_top_inner(
    inner_width: int,
    fill: str,
    prefix: str,
    suffix: str,
    title: str,
    align: Literal["left", "center", "right"],
) -> str:
    """Compose the horizontal edge with prefix (left decorations), title, suffix (right decorations).

    Strategy:
      - prefix is always attached at the very start.
      - suffix is always attached at the very end.
      - title takes the aligned slot in between, surrounded by fill-char padding.
    """
    if inner_width <= 0:
        return ""
    space_for_title = inner_width - len(prefix) - len(suffix)
    if space_for_title <= 0:
        # Prefix/suffix alone don't fit — truncate whole line.
        return (prefix + fill * inner_width + suffix)[:inner_width]

    if not title or len(title) > space_for_title:
        title = _truncate(title, space_for_title) if title else ""

    padding_total = space_for_title - len(title)
    if align == "left":
        left_pad = 0
        right_pad = padding_total
    elif align == "right":
        left_pad = padding_total
        right_pad = 0
    else:  # center
        left_pad = padding_total // 2
        right_pad = padding_total - left_pad

    return prefix + (fill * left_pad) + title + (fill * right_pad) + suffix


def render_bottom(
    width: int,
    style: BorderStyle,
    sides: BorderSides,
    decorations: Decorations,
) -> str:
    """Bottom row of the frame. Contains subtitle and resize grip."""
    if not sides.bottom or width <= 0:
        return ""

    chars = _chars(style)
    left_corner = chars["bl"] if sides.left else chars["b"]
    right_corner = chars["br"] if sides.right else chars["b"]
    fill = chars["b"]

    inner_width = width - (1 if sides.left else 0) - (1 if sides.right else 0)
    if inner_width < 0:
        return (left_corner + right_corner)[:width]

    # Resize grip replaces the bottom-right corner glyph with a "┘" if enabled.
    if decorations.resize_grip and sides.right:
        right_corner = "┘"

    subtitle = decorations.subtitle or ""
    subtitle_text = ""
    if subtitle:
        subtitle_text = f" {subtitle} "

    # Subtitle is centered by default on the bottom edge.
    inner = _compose_top_inner(
        inner_width=inner_width,
        fill=fill,
        prefix="",
        suffix="",
        title=subtitle_text,
        align="center",
    )

    result = f"{left_corner if sides.left else ''}{inner}{right_corner if sides.right else ''}"
    if len(result) < width:
        result += fill * (width - len(result))
    return result[:width]


def render_left_char(style: BorderStyle, sides: BorderSides) -> str:
    if not sides.left:
        return ""
    return _chars(style)["l"]


def render_right_char(style: BorderStyle, sides: BorderSides) -> str:
    if not sides.right:
        return ""
    return _chars(style)["r"]


def frame_margin(sides: BorderSides) -> tuple[int, int, int, int]:
    """Return (top, right, bottom, left) cell margins that the border consumes."""
    return (
        1 if sides.top else 0,
        1 if sides.right else 0,
        1 if sides.bottom else 0,
        1 if sides.left else 0,
    )


def effective_border(focused: bool, focused_style: BorderStyle, unfocused_style: BorderStyle) -> BorderStyle:
    return focused_style if focused else unfocused_style
