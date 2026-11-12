"""Soft wrap (Alt+Z): long lines folded onto display rows, buffer untouched."""

from textual.app import App, ComposeResult

from dunders.windowing.core.buffer import TextBuffer
from dunders.windowing.editor.content import EditorContent
from dunders.windowing.editor.widget import EditorWidget, wrap_points
from dunders.windowing.palette import Palette
from dunders.windowing.themes.modern_dark import modern_dark

WORDS = " ".join(f"word{i:02d}" for i in range(40))  # 279 chars, spaces every 7


class _Host(App):
    def __init__(self, text: str) -> None:
        super().__init__()
        self._buf = TextBuffer.from_string(text)
        self._palette = Palette(modern_dark)

    def compose(self) -> ComposeResult:
        yield EditorWidget(buffer=self._buf, palette=self._palette, soft_wrap=True)


def _segments(ed: EditorWidget, buf_row: int) -> list[str]:
    return [
        ed._rendered_lines[i][ed._seg_start[i]:ed._seg_end[i]]
        for i, r in enumerate(ed._line_map) if r == buf_row
    ]


# --- pure wrap_points ------------------------------------------------------

def test_short_line_is_one_segment():
    assert wrap_points("hello", 10) == [0]
    assert wrap_points("", 10) == [0]
    assert wrap_points("x" * 10, 10) == [0]


def test_breaks_after_last_space_that_fits():
    # "aaa bbb " fits in 8; the space stays on the upper segment.
    assert wrap_points("aaa bbb ccc", 8) == [0, 8]
    assert wrap_points("aaa bbb ccc", 6) == [0, 4, 8]


def test_word_longer_than_width_is_cut_hard():
    assert wrap_points("x" * 25, 10) == [0, 10, 20]


def test_segments_never_exceed_width():
    line = "ab cdefghijklmnop q rs " * 50
    pts = wrap_points(line, 9) + [len(line)]
    assert all(0 < b - a <= 9 for a, b in zip(pts, pts[1:]))


# --- widget ----------------------------------------------------------------

async def test_wrap_splits_display_but_not_buffer():
    app = _Host(f"short\n{WORDS}\nend")
    async with app.run_test(size=(40, 12)):
        ed = app.query_one(EditorWidget)
        width = ed._wrap_width()
        segs = _segments(ed, 1)
        assert len(segs) > 1
        assert "".join(segs) == WORDS
        assert all(len(s) <= width for s in segs)
        # Words are not split across rows.
        assert all(s.endswith(" ") for s in segs[:-1])
        assert ed.buffer.lines == ["short", WORDS, "end"]
        assert ed.virtual_size.width <= ed.size.width


async def test_down_up_walk_segments_and_keep_column():
    app = _Host(f"{WORDS}\nend")
    async with app.run_test(size=(40, 12)):
        ed = app.query_one(EditorWidget)
        ed.buffer.cursor_row, ed.buffer.cursor_col = 0, 3
        ed.action_cursor_down()
        assert ed.buffer.cursor_row == 0  # same buffer line, next segment
        assert ed._display_pos(0, ed.buffer.cursor_col) == (1, 3)
        ed.action_cursor_up()
        assert (ed.buffer.cursor_row, ed.buffer.cursor_col) == (0, 3)
        # Walking down past the last segment reaches the next buffer line.
        for _ in range(len(_segments(ed, 0))):
            ed.action_cursor_down()
        assert ed.buffer.cursor_row == 1


async def test_home_end_by_segment_then_whole_line():
    app = _Host(WORDS)
    async with app.run_test(size=(40, 12)):
        ed = app.query_one(EditorWidget)
        first_end = ed._seg_end[0]
        ed.buffer.cursor_col = 2
        ed.action_line_end()
        assert ed.buffer.cursor_col == first_end - 1  # last cell of row 0
        ed.action_line_end()
        assert ed.buffer.cursor_col == len(WORDS)
        ed.action_line_start()
        assert ed.buffer.cursor_col == ed._seg_start[-1]
        ed.action_line_start()
        assert ed.buffer.cursor_col == 0


async def test_continuation_rows_show_wrap_marker_in_gutter():
    app = _Host(WORDS)
    async with app.run_test(size=(40, 12)):
        ed = app.query_one(EditorWidget)
        first = ed.render_line(0).text
        second = ed.render_line(1).text
        assert first.lstrip().startswith("1 ")
        assert second.lstrip().startswith("↪ ")
        assert ed._seg_start is not None
        start, end = ed._seg_start[1], ed._seg_end[1]
        assert WORDS[start:end].rstrip() in second


async def test_click_on_continuation_row_maps_into_the_line():
    app = _Host(WORDS)
    async with app.run_test(size=(40, 12)):
        ed = app.query_one(EditorWidget)
        gutter = ed._gutter_width()
        row, col = ed._mouse_to_buffer_pos(gutter + 2, 1)
        assert (row, col) == (0, ed._seg_start[1] + 2)


async def test_toggle_off_restores_one_row_per_line():
    app = _Host(f"{WORDS}\nend")
    async with app.run_test(size=(40, 12)):
        ed = app.query_one(EditorWidget)
        ed.set_soft_wrap(False)
        assert ed._seg_start is None
        assert len(ed._rendered_lines) == 2
        ed.set_soft_wrap(True)
        assert len(ed._rendered_lines) > 2


async def test_resize_rewraps():
    app = _Host(WORDS)
    async with app.run_test(size=(40, 12)) as pilot:
        ed = app.query_one(EditorWidget)
        narrow_rows = len(ed._rendered_lines)
        await pilot.resize_terminal(100, 12)
        await pilot.pause()
        assert len(ed._rendered_lines) < narrow_rows


class _ContentHost(App):
    def compose(self) -> ComposeResult:
        yield EditorContent(initial_text=WORDS)


async def test_alt_z_toggles_wrap_in_editor_content():
    app = _ContentHost()
    async with app.run_test(size=(40, 12)) as pilot:
        content = app.query_one(EditorContent)
        ed = content._editor
        ed.focus()
        assert not ed.soft_wrap
        await pilot.press("alt+z")
        assert ed.soft_wrap
        await pilot.press("alt+z")
        assert not ed.soft_wrap
        assert "toggle_wrap" in [c.id for c in content.get_commands()]
