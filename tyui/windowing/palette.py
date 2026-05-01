"""Color palette and theming: named roles, fallback hierarchy, runtime themes."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal, Protocol

from rich.style import Style as RichStyle

from .frame import BorderStyle


@dataclass(frozen=True)
class Style:
    """Platform-neutral style spec. Converts to rich.style.Style on demand."""

    fg: str | None = None
    bg: str | None = None
    bold: bool = False
    dim: bool = False
    italic: bool = False
    underline: bool = False
    reverse: bool = False

    def to_rich(self) -> RichStyle:
        return RichStyle(
            color=self.fg,
            bgcolor=self.bg,
            bold=self.bold if self.bold else None,
            dim=self.dim if self.dim else None,
            italic=self.italic if self.italic else None,
            underline=self.underline if self.underline else None,
            reverse=self.reverse if self.reverse else None,
        )

    def merge(self, other: "Style") -> "Style":
        """Return a new Style where non-None fields of `other` override self."""
        return Style(
            fg=other.fg if other.fg is not None else self.fg,
            bg=other.bg if other.bg is not None else self.bg,
            bold=other.bold or self.bold,
            dim=other.dim or self.dim,
            italic=other.italic or self.italic,
            underline=other.underline or self.underline,
            reverse=other.reverse or self.reverse,
        )


# --- Background patterns ----------------------------------------------------


class BackgroundPattern(Protocol):
    def render_row(self, y: int, width: int) -> str: ...


@dataclass(frozen=True)
class SolidBackground:
    """Fills with space characters. The background colour comes from the palette."""

    def render_row(self, y: int, width: int) -> str:
        return " " * max(0, width)


@dataclass(frozen=True)
class DotBackground:
    char: str = "▒"

    def render_row(self, y: int, width: int) -> str:
        return self.char * max(0, width)


@dataclass(frozen=True)
class GridBackground:
    step_x: int = 4
    step_y: int = 2
    dot: str = "·"

    def render_row(self, y: int, width: int) -> str:
        if width <= 0:
            return ""
        if y % self.step_y != 0:
            return " " * width
        row = []
        for x in range(width):
            row.append(self.dot if x % self.step_x == 0 else " ")
        return "".join(row)


# --- Theme / Palette --------------------------------------------------------


@dataclass(frozen=True)
class Theme:
    """Immutable mapping of semantic roles to styles plus border/background defaults."""

    name: str = "default"
    styles: dict[str, Style] = field(default_factory=dict)
    border_focused: BorderStyle = BorderStyle.DOUBLE
    border_unfocused: BorderStyle = BorderStyle.SINGLE
    background_pattern: BackgroundPattern = field(default_factory=SolidBackground)

    def resolve(self, role: str) -> Style:
        """Walk the role hierarchy from most specific to least until a match is found.

        `window.title.focused.hover` → `window.title.focused` → `window.title` →
        `window` → empty Style.
        """
        parts = role.split(".") if role else []
        while parts:
            key = ".".join(parts)
            if key in self.styles:
                return self.styles[key]
            parts.pop()
        return self.styles.get("", Style())


class Palette:
    """Runtime resolver: holds active theme + optional overrides."""

    __slots__ = ("theme", "overrides")

    def __init__(self, theme: Theme, overrides: dict[str, Style] | None = None) -> None:
        self.theme = theme
        self.overrides: dict[str, Style] = dict(overrides or {})

    def get(self, role: str) -> Style:
        if role in self.overrides:
            return self.overrides[role]
        return self.theme.resolve(role)

    def with_override(self, role: str, style: Style) -> "Palette":
        new = Palette(self.theme, self.overrides)
        new.overrides[role] = style
        return new

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme

    def rich_style(self, role: str) -> RichStyle:
        return self.get(role).to_rich()
