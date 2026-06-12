"""TOML-based theme loader with auto-discovery."""

from __future__ import annotations

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

USER_THEMES_DIR = Path.home() / ".config" / "dunders" / "windowing" / "themes"

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
        style = _parse_style(spec)
        # Validate colours eagerly. Rich parses colours when the style is built,
        # so an invalid value (e.g. a 5-digit hex like "#11111") only blows up at
        # render time — the first time a widget paints this role, which for an
        # editor/menu colour means a crash deep in the UI loop. Surfacing it as a
        # ThemeLoadError here lets the app's theme guards fall back gracefully.
        try:
            style.to_rich()
        except Exception as exc:
            raise ThemeLoadError(
                f"Invalid colour in theme {p} (role '{role}'): {exc}"
            ) from exc
        styles[role] = style

    return Theme(
        name=theme_block.get("name", p.stem),
        styles=styles,
        border_focused=_parse_border(borders_block.get("focused", "double"), BorderStyle.DOUBLE),
        border_unfocused=_parse_border(borders_block.get("unfocused", "single"), BorderStyle.SINGLE),
        background_pattern=_parse_pattern(pattern_block),
    )


def resolve_theme_path(name: str) -> Path | None:
    """File path of theme ``name``, or None if it has no editable file.

    Mirrors the search order in :func:`load_theme` (user themes dir, then the
    bundled examples/). Returns None for ``modern_dark`` (a Python object with
    no TOML) and for unknown names.
    """
    if name == "modern_dark":
        return None
    candidate = USER_THEMES_DIR / f"{name}.toml"
    if candidate.exists():
        return candidate
    examples = Path(__file__).parent / "examples" / f"{name}.toml"
    if examples.exists():
        return examples
    return None


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
