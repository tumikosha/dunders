"""Footer buttons of a file panel: copy name, copy full path, insert name."""

import shlex

import pytest

from dunders.app import DundersApp
from dunders.fm.file_panel import FilePanel


def _cursor_to(panel, name) -> None:
    panel.cursor = next(i for i, e in enumerate(panel.entries) if e.name == name)
    panel.refresh()


def _button_x(panel, action) -> int:
    width = panel.size.width
    for start, _end, act, _glyph in panel._footer_button_spans(width):
        if act == action:
            return start + 1
    raise AssertionError(f"no footer button {action!r}")


def _footer_text(panel) -> str:
    strip = panel.render_line(panel.size.height - 1)
    return "".join(seg.text for seg in strip)


@pytest.fixture
def copied(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        "dunders.app.clipboard.copy", lambda text, app=None: calls.append(text)
    )
    return calls


def test_footer_renders_buttons_before_name(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    _cursor_to(p, "a.txt")
    text = "".join(seg.text for seg in p._render_footer(40))
    assert text.startswith(" ⧉ N ⧉ P >_ a.txt")
    assert len(text) == 40


def test_footer_hides_buttons_when_narrow(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    _cursor_to(p, "a.txt")
    assert p._footer_button_spans(20) == []
    text = "".join(seg.text for seg in p._render_footer(20))
    assert text.startswith(" a.txt")


def test_footer_button_spans_match_rendered_glyphs(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    _cursor_to(p, "a.txt")
    text = "".join(seg.text for seg in p._render_footer(60))
    for start, end, _action, glyph in p._footer_button_spans(60):
        assert text[start:end] == glyph


async def _click_footer(pilot, panel, action) -> None:
    await pilot.click(panel, offset=(_button_x(panel, action), panel.size.height - 1))
    await pilot.pause()


@pytest.mark.asyncio
async def test_copy_name_button(tmp_path, copied):
    (tmp_path / "a b.txt").write_text("x")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        panel = app._active_panel()
        _cursor_to(panel, "a b.txt")
        assert "⧉ N" in _footer_text(panel)
        await _click_footer(pilot, panel, "copy_name")
        assert copied == ["a b.txt"]


@pytest.mark.asyncio
async def test_copy_path_button(tmp_path, copied):
    (tmp_path / "a.txt").write_text("x")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        panel = app._active_panel()
        _cursor_to(panel, "a.txt")
        await _click_footer(pilot, panel, "copy_path")
        assert copied == [str(tmp_path / "a.txt")]


@pytest.mark.asyncio
async def test_copy_buttons_on_parent_row_use_current_dir(tmp_path, copied):
    sub = tmp_path / "sub"
    sub.mkdir()
    app = DundersApp(launch_mode="fm", initial_path=str(sub))
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        panel = app._active_panel()
        _cursor_to(panel, "..")
        await _click_footer(pilot, panel, "copy_name")
        await _click_footer(pilot, panel, "copy_path")
        assert copied[0] == "sub"
        assert copied[1].endswith("/sub")


@pytest.mark.asyncio
async def test_insert_button_inserts_at_command_line_cursor(tmp_path):
    (tmp_path / "a b.txt").write_text("x")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        panel = app._active_panel()
        app.command_line.insert_at_cursor("cat ")
        _cursor_to(panel, "a b.txt")
        await _click_footer(pilot, panel, "insert")
        assert app.command_line.text == "cat " + shlex.quote("a b.txt") + " "


@pytest.mark.asyncio
async def test_footer_button_highlights_on_hover(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        panel = app._active_panel()
        _cursor_to(panel, "a.txt")
        footer_y = panel.size.height - 1
        spans = panel._footer_button_spans(panel.size.width)

        def glyph_styles():
            strip = panel.render_line(footer_y)
            return {seg.text: seg.style for seg in strip if seg.text in
                    {g for _s, _e, _a, g in spans}}

        idle = glyph_styles()
        await pilot.hover(panel, offset=(_button_x(panel, "copy_path"), footer_y))
        await pilot.pause()
        assert panel._hover_footer == "copy_path"
        hovered = glyph_styles()
        assert hovered["⧉ P"] != idle["⧉ P"]
        assert hovered["⧉ N"] == idle["⧉ N"]

        await pilot.hover(panel, offset=(60, footer_y))  # over the name
        await pilot.pause()
        assert panel._hover_footer is None
        assert glyph_styles() == idle
