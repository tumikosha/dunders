"""Unit tests for palette, themes, and TOML loader."""

from pathlib import Path

import pytest

from tyui.windowing.frame import BorderStyle
from tyui.windowing.palette import (
    DotBackground,
    GridBackground,
    Palette,
    SolidBackground,
    Style,
    Theme,
)
from tyui.windowing.themes import modern_dark
from tyui.windowing.themes.loader import (
    ThemeLoadError,
    list_themes,
    load_theme,
    theme_registry,
)


class TestStyle:
    def test_defaults_empty(self):
        s = Style()
        assert s.fg is None and s.bg is None
        assert not (s.bold or s.dim or s.italic or s.underline or s.reverse)

    def test_to_rich(self):
        rs = Style(fg="red", bg="blue", bold=True).to_rich()
        assert rs.color.name == "red"
        assert rs.bgcolor.name == "blue"
        assert rs.bold is True

    def test_merge(self):
        base = Style(fg="red", bold=True)
        over = Style(fg="green")
        out = base.merge(over)
        assert out.fg == "green"  # override wins
        assert out.bold is True   # preserved


class TestBackgroundPatterns:
    def test_solid(self):
        p = SolidBackground()
        assert p.render_row(0, 5) == "     "

    def test_dot(self):
        p = DotBackground(char="·")
        assert p.render_row(0, 3) == "···"

    def test_grid(self):
        p = GridBackground(step_x=2, step_y=1, dot="*")
        row = p.render_row(0, 6)
        assert row == "* * * "


class TestThemeFallback:
    def test_exact_match(self):
        theme = Theme(styles={"window.title.focused": Style(fg="yellow")})
        assert theme.resolve("window.title.focused").fg == "yellow"

    def test_parent_fallback(self):
        theme = Theme(styles={"window.title": Style(fg="yellow")})
        assert theme.resolve("window.title.focused").fg == "yellow"

    def test_grandparent_fallback(self):
        theme = Theme(styles={"window": Style(fg="white")})
        assert theme.resolve("window.title.focused").fg == "white"

    def test_unknown_returns_default_style(self):
        theme = Theme(styles={})
        assert theme.resolve("nonexistent") == Style()


class TestPalette:
    def test_get_from_theme(self):
        t = Theme(styles={"x": Style(fg="red")})
        p = Palette(t)
        assert p.get("x").fg == "red"

    def test_override_wins(self):
        t = Theme(styles={"x": Style(fg="red")})
        p = Palette(t, overrides={"x": Style(fg="green")})
        assert p.get("x").fg == "green"

    def test_with_override_is_independent_copy(self):
        p = Palette(Theme(), overrides={"a": Style(fg="red")})
        p2 = p.with_override("a", Style(fg="blue"))
        assert p.get("a").fg == "red"
        assert p2.get("a").fg == "blue"


class TestModernDark:
    def test_theme_has_core_roles(self):
        for role in [
            "desktop.background",
            "window.border.focused",
            "window.title.focused",
            "icon_tray.background",
            "modal.overlay",
        ]:
            assert role in modern_dark.styles, f"missing role: {role}"

    def test_border_defaults(self):
        assert modern_dark.border_focused == BorderStyle.DOUBLE
        assert modern_dark.border_unfocused == BorderStyle.SINGLE


class TestTOMLLoader:
    def test_load_example_turbo_blue(self):
        theme = load_theme("turbo_blue")
        assert theme.name == "turbo_blue"
        assert theme.border_focused == BorderStyle.DOUBLE
        assert isinstance(theme.background_pattern, DotBackground)
        assert theme.styles["window.title.focused"].bold is True
        assert theme.styles["desktop.background"].bg == "#0000aa"

    def test_unknown_theme_raises(self):
        with pytest.raises(ThemeLoadError):
            load_theme("does_not_exist_anywhere")

    def test_list_themes_includes_builtin_and_example(self):
        names = list_themes()
        assert "modern_dark" in names
        assert "turbo_blue" in names

    def test_registry_caches(self):
        theme_registry.invalidate()
        t1 = theme_registry.get("turbo_blue")
        t2 = theme_registry.get("turbo_blue")
        assert t1 is t2

    def test_load_from_path(self, tmp_path: Path):
        p = tmp_path / "mytheme.toml"
        p.write_text(
            '[theme]\nname = "mini"\n'
            '[theme.borders]\nfocused = "heavy"\nunfocused = "single"\n'
            '[theme.pattern]\nkind = "grid"\nstep_x = 3\nstep_y = 2\ndot = "+"\n'
            '[styles."window.title"]\nfg = "#ff0000"\nbold = true\n'
        )
        theme = load_theme(p)
        assert theme.name == "mini"
        assert theme.border_focused == BorderStyle.HEAVY
        assert isinstance(theme.background_pattern, GridBackground)
        assert theme.styles["window.title"].fg == "#ff0000"

    def test_malformed_toml_raises(self, tmp_path: Path):
        p = tmp_path / "broken.toml"
        p.write_text("this is not = valid [[[ toml")
        with pytest.raises(ThemeLoadError):
            load_theme(p)
