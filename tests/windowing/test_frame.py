"""Unit tests for tyui.windowing.frame — pure rendering primitives."""

import pytest

from tyui.windowing.frame import (
    BorderSides,
    BorderStyle,
    Decorations,
    TitleSpec,
    effective_border,
    frame_margin,
    render_bottom,
    render_left_char,
    render_right_char,
    render_top,
)


class TestBorderSides:
    def test_all(self):
        s = BorderSides.all()
        assert s.top and s.right and s.bottom and s.left

    def test_none(self):
        s = BorderSides.none()
        assert not (s.top or s.right or s.bottom or s.left)
        assert not s.has_any()

    def test_only(self):
        s = BorderSides.only("top", "right")
        assert s.top and s.right
        assert not s.bottom and not s.left

    def test_frame_margin(self):
        assert frame_margin(BorderSides.all()) == (1, 1, 1, 1)
        assert frame_margin(BorderSides.none()) == (0, 0, 0, 0)
        assert frame_margin(BorderSides.only("top")) == (1, 0, 0, 0)


class TestRenderTop:
    def test_single_empty_title(self):
        out = render_top(10, BorderStyle.SINGLE, BorderSides.all(), TitleSpec(""), Decorations())
        assert out == "┌────────┐"
        assert len(out) == 10

    def test_double_empty_title(self):
        out = render_top(10, BorderStyle.DOUBLE, BorderSides.all(), TitleSpec(""), Decorations())
        assert out == "╔════════╗"

    def test_rounded(self):
        out = render_top(6, BorderStyle.ROUNDED, BorderSides.all(), TitleSpec(""), Decorations())
        assert out == "╭────╮"

    def test_heavy(self):
        out = render_top(6, BorderStyle.HEAVY, BorderSides.all(), TitleSpec(""), Decorations())
        assert out == "┏━━━━┓"

    def test_ascii(self):
        out = render_top(6, BorderStyle.ASCII, BorderSides.all(), TitleSpec(""), Decorations())
        assert out == "+----+"

    def test_title_left(self):
        out = render_top(
            20, BorderStyle.SINGLE, BorderSides.all(),
            TitleSpec("Hi", align="left", padding=1), Decorations()
        )
        # ┌ Hi ──────────────┐
        assert out.startswith("┌ Hi ")
        assert out.endswith("┐")
        assert len(out) == 20

    def test_title_center(self):
        out = render_top(
            20, BorderStyle.SINGLE, BorderSides.all(),
            TitleSpec("X", align="center", padding=1), Decorations()
        )
        assert len(out) == 20
        # Symmetric padding around the title
        assert "─ X ─" in out

    def test_title_right(self):
        out = render_top(
            20, BorderStyle.SINGLE, BorderSides.all(),
            TitleSpec("End", align="right", padding=1), Decorations()
        )
        assert out.endswith(" End ┐")
        assert len(out) == 20

    def test_title_truncated(self):
        out = render_top(
            8, BorderStyle.SINGLE, BorderSides.all(),
            TitleSpec("VeryLongTitle"), Decorations()
        )
        assert len(out) == 8
        assert "…" in out

    def test_no_top_returns_empty(self):
        out = render_top(10, BorderStyle.SINGLE, BorderSides.only("bottom"), TitleSpec(""), Decorations())
        assert out == ""

    def test_no_left_side_omits_corner(self):
        out = render_top(10, BorderStyle.SINGLE, BorderSides.only("top", "right"), TitleSpec(""), Decorations())
        assert len(out) == 10
        # Right corner present, left corner gone
        assert out.endswith("┐")
        assert not out.startswith("┌")

    def test_close_box(self):
        out = render_top(
            20, BorderStyle.SINGLE, BorderSides.all(),
            TitleSpec("Name", align="left"), Decorations(close_box=True)
        )
        assert "[■]" in out
        assert len(out) == 20

    def test_zoom_box(self):
        out = render_top(
            20, BorderStyle.SINGLE, BorderSides.all(),
            TitleSpec(""), Decorations(zoom_box=True)
        )
        assert out.endswith("[↕]┐")

    def test_number(self):
        out = render_top(
            20, BorderStyle.SINGLE, BorderSides.all(),
            TitleSpec(""), Decorations(number=1)
        )
        assert "─ 1 ─" in out


class TestRenderBottom:
    def test_single(self):
        out = render_bottom(10, BorderStyle.SINGLE, BorderSides.all(), Decorations())
        assert out == "└────────┘"

    def test_double(self):
        out = render_bottom(10, BorderStyle.DOUBLE, BorderSides.all(), Decorations())
        assert out == "╚════════╝"

    def test_resize_grip(self):
        out = render_bottom(10, BorderStyle.SINGLE, BorderSides.all(), Decorations(resize_grip=True))
        assert out.endswith("┘")  # grip replaces br glyph (which is also ┘ for SINGLE)
        assert len(out) == 10

    def test_resize_grip_on_double(self):
        # For DOUBLE the br is ╝ — resize_grip should replace it with ┘
        out = render_bottom(10, BorderStyle.DOUBLE, BorderSides.all(), Decorations(resize_grip=True))
        assert out.endswith("┘")
        assert not out.endswith("╝")

    def test_subtitle(self):
        out = render_bottom(
            20, BorderStyle.SINGLE, BorderSides.all(),
            Decorations(subtitle="42 lines")
        )
        assert "42 lines" in out
        assert len(out) == 20

    def test_no_bottom(self):
        out = render_bottom(10, BorderStyle.SINGLE, BorderSides.only("top"), Decorations())
        assert out == ""


class TestLeftRightChars:
    def test_single(self):
        assert render_left_char(BorderStyle.SINGLE, BorderSides.all()) == "│"
        assert render_right_char(BorderStyle.SINGLE, BorderSides.all()) == "│"

    def test_double(self):
        assert render_left_char(BorderStyle.DOUBLE, BorderSides.all()) == "║"
        assert render_right_char(BorderStyle.DOUBLE, BorderSides.all()) == "║"

    def test_heavy(self):
        assert render_left_char(BorderStyle.HEAVY, BorderSides.all()) == "┃"

    def test_no_left(self):
        assert render_left_char(BorderStyle.SINGLE, BorderSides.only("top")) == ""


class TestEffectiveBorder:
    def test_focused(self):
        assert effective_border(True, BorderStyle.DOUBLE, BorderStyle.SINGLE) == BorderStyle.DOUBLE

    def test_unfocused(self):
        assert effective_border(False, BorderStyle.DOUBLE, BorderStyle.SINGLE) == BorderStyle.SINGLE
