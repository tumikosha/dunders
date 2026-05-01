"""TOML-based theme loader with auto-discovery."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from ..frame import BorderStyle
from ..palette import (
    BackgroundPattern,
    DotBackground,
    GridBackground,
    SolidBackground,
    Style,
    Theme,
)

USER_THEMES_DIR = Path.home() / ".config" / "tyui" / "windowing" / "themes"

_BORDER_STYLE_NAMES = {s.value: s for s in BorderStyle}


class ThemeLoadError(ValueError):
    """Raised when a TOML theme file cannot be parsed into a Theme."""


def _parse_style(data: dict) -> Style:
    return Style(
        fg=data.get("fg"),
        bg=data.get("bg"),
        bold=bool(data.get("bold", False)),
        dim=bool(data.get("dim", False)),
        italic=bool(data.get("italic", False)),
        underline=bool(data.get("underline", False)),
        reverse=bool(data.get("reverse", False)),
    )


def _parse_border(value: str, default: BorderStyle) -> BorderStyle:
    return _BORDER_STYLE_NAMES.get(value.lower(), default)


def _parse_pattern(data: dict | None) -> BackgroundPattern:
    if not data:
        return SolidBackground()
    kind = (data.get("kind") or "solid").lower()
    if kind == "solid":
        return SolidBackground()
    if kind == "dots":
        return DotBackground(char=data.get("char", "▒"))
    if kind == "grid":
        return GridBackground(
            step_x=int(data.get("step_x", 4)),
            step_y=int(data.get("step_y", 2)),
            dot=data.get("dot", "·"),
        )
    return SolidBackground()


def load_theme(path_or_name: str | Path) -> Theme:
    """Load a theme from a TOML file path or a theme name.

    If passed a name (no path separators), searches in user themes dir and
    examples/. Raises ThemeLoadError if not found or malformed.
    """
    p = Path(path_or_name)
    if not p.exists():
        # Try user-themes dir first.
        candidate = USER_THEMES_DIR / f"{path_or_name}.toml"
        if candidate.exists():
            p = candidate
        else:
            examples = Path(__file__).parent / "examples" / f"{path_or_name}.toml"
            if examples.exists():
                p = examples
            else:
                raise ThemeLoadError(f"Theme not found: {path_or_name}")

    try:
        with open(p, "rb") as f:
            data = tomllib.load(f)
    except OSError as exc:
        raise ThemeLoadError(f"Cannot read theme file: {p}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ThemeLoadError(f"Malformed theme TOML: {p}: {exc}") from exc

    theme_block = data.get("theme", {})
    borders_block = theme_block.get("borders", {})
    pattern_block = theme_block.get("pattern")
    styles_block = data.get("styles", {})

    styles: dict[str, Style] = {}
    for role, spec in styles_block.items():
        if not isinstance(spec, dict):
            continue
        styles[role] = _parse_style(spec)

    return Theme(
        name=theme_block.get("name", p.stem),
        styles=styles,
        border_focused=_parse_border(borders_block.get("focused", "double"), BorderStyle.DOUBLE),
        border_unfocused=_parse_border(borders_block.get("unfocused", "single"), BorderStyle.SINGLE),
        background_pattern=_parse_pattern(pattern_block),
    )


def list_themes() -> list[str]:
    """List available theme names: built-in + user + examples."""
    names: set[str] = {"modern_dark"}
    if USER_THEMES_DIR.exists():
        for p in USER_THEMES_DIR.glob("*.toml"):
            names.add(p.stem)
    examples_dir = Path(__file__).parent / "examples"
    if examples_dir.exists():
        for p in examples_dir.glob("*.toml"):
            names.add(p.stem)
    return sorted(names)


class _ThemeRegistry:
    """Small cache so themes can be loaded by name without re-parsing."""

    def __init__(self) -> None:
        self._cache: dict[str, Theme] = {}

    def get(self, name: str) -> Theme:
        if name in self._cache:
            return self._cache[name]
        if name == "modern_dark":
            from .modern_dark import modern_dark
            theme = modern_dark
        else:
            theme = load_theme(name)
        self._cache[name] = theme
        return theme

    def invalidate(self, name: str | None = None) -> None:
        if name is None:
            self._cache.clear()
        else:
            self._cache.pop(name, None)


theme_registry = _ThemeRegistry()
