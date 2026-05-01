"""Tests for EditorContent."""

import pytest
from textual.app import App, ComposeResult

from tyui.windowing.editor.content import EditorContent
from tyui.windowing.editor.splitter import Splitter
from tyui.windowing.content import WindowCommand


def test_editor_content_creates_with_defaults():
    content = EditorContent()
    assert content._editor is not None
    assert content.window_title is None


def test_editor_content_with_initial_text():
    content = EditorContent(initial_text="hello\nworld")
    assert content._editor.buffer.lines == ["hello", "world"]


def test_editor_content_with_title():
    content = EditorContent(title="My Editor")
    assert content.window_title == "My Editor"


def test_editor_content_get_commands():
    content = EditorContent(enable_macros=True)
    commands = content.get_commands()
    cmd_ids = [c.id for c in commands]
    assert "save" in cmd_ids
    assert "find" in cmd_ids


def test_editor_content_macro_storage_path():
    content = EditorContent(macro_storage_path="/tmp/macros")
    assert content._macro_storage_path == "/tmp/macros"


def test_editor_content_get_commands_has_split():
    content = EditorContent()
    cmd_ids = [c.id for c in content.get_commands()]
    assert "split_h" in cmd_ids
    assert "split_v" in cmd_ids


def test_editor_content_is_split_default():
    content = EditorContent()
    assert content.is_split is False


class _EditorApp(App):
    def __init__(self, content: EditorContent) -> None:
        super().__init__()
        self.content = content

    def compose(self) -> ComposeResult:
        yield self.content


def _layout(content: EditorContent) -> str:
    return content._current_layout_name()


@pytest.mark.asyncio
async def test_toggle_split_horizontal_stacks_panes_vertically():
    """Horizontal divider → panes top/bottom → Textual vertical layout."""
    content = EditorContent(initial_text="line1\nline2\nline3")
    app = _EditorApp(content)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert content.is_split is False
        content.toggle_split("horizontal")
        await pilot.pause()
        assert content.is_split is True
        assert content._editor2 is not None
        assert content._editor2.buffer is content._editor.buffer
        assert _layout(content) == "vertical"

        content.toggle_split("horizontal")
        await pilot.pause()
        assert content.is_split is False
        assert content._editor2 is None


@pytest.mark.asyncio
async def test_toggle_split_vertical_places_panes_side_by_side():
    """Vertical divider → panes side-by-side → Textual horizontal layout."""
    content = EditorContent(initial_text="a\nb")
    app = _EditorApp(content)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        content.toggle_split("vertical")
        await pilot.pause()
        assert content.is_split
        assert _layout(content) == "horizontal"


@pytest.mark.asyncio
async def test_toggle_split_switches_orientation_when_already_split():
    content = EditorContent(initial_text="x\ny")
    app = _EditorApp(content)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        content.toggle_split("horizontal")
        await pilot.pause()
        assert _layout(content) == "vertical"
        # Asking for vertical while already in horizontal should switch, not close.
        content.toggle_split("vertical")
        await pilot.pause()
        assert content.is_split is True
        assert _layout(content) == "horizontal"
        # And original editor should still be mounted (no tearing-down regression).
        assert content._editor.is_mounted
        assert content._editor2 is not None and content._editor2.is_mounted


@pytest.mark.asyncio
async def test_split_editors_share_buffer_edits():
    content = EditorContent(initial_text="hello")
    app = _EditorApp(content)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        content.toggle_split("horizontal")
        await pilot.pause()
        ed1 = content._editor
        ed2 = content._editor2
        ed1.buffer.insert_char("X")
        ed1._post_buffer_update()
        await pilot.pause()
        assert ed2.buffer.lines == ed1.buffer.lines
        assert ed2.buffer.lines[0].startswith("X")


@pytest.mark.asyncio
async def test_split_mounts_visible_splitter():
    content = EditorContent(initial_text="a\nb\nc")
    app = _EditorApp(content)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        content.toggle_split("vertical")  # side-by-side → vertical bar
        await pilot.pause()
        assert content._splitter is not None
        assert content._splitter.is_mounted
        assert content._splitter.direction == "v-divider"

        content.toggle_split("horizontal")  # switch → bar flips
        await pilot.pause()
        assert content._splitter is not None
        assert content._splitter.direction == "h-divider"

        content.toggle_split("horizontal")  # close
        await pilot.pause()
        assert content._splitter is None


@pytest.mark.asyncio
async def test_splitter_drag_resizes_first_editor():
    content = EditorContent(initial_text="one\ntwo")
    app = _EditorApp(content)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        content.toggle_split("vertical")  # side-by-side
        await pilot.pause()
        before_w = content._editor.size.width
        # Simulate a drag: post Dragged directly so we don't depend on mouse pilot.
        content._split_container.post_message(
            Splitter.Dragged(content._splitter, dx=5, dy=0)
        )
        await pilot.pause()
        # Width style should be set to an integer > before_w.
        w_style = content._editor.styles.width
        assert w_style is not None
        # Textual stores as Scalar; pull the numeric value.
        val = getattr(w_style, "value", None)
        assert val is not None and val >= before_w + 5 - 1  # allow ±1 layout rounding


@pytest.mark.asyncio
async def test_unsplit_resets_editor_size():
    content = EditorContent(initial_text="one\ntwo")
    app = _EditorApp(content)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        content.toggle_split("vertical")
        await pilot.pause()
        # Drag to resize
        content._split_container.post_message(
            Splitter.Dragged(content._splitter, dx=10, dy=0)
        )
        await pilot.pause()
        # Close split — sizes should revert to 1fr (unit = "fr").
        content.toggle_split("vertical")
        await pilot.pause()
        w_style = content._editor.styles.width
        unit = getattr(w_style, "unit", None)
        assert unit is not None and getattr(unit, "name", "") == "FRACTION"
