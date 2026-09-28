"""Editor performance and correctness on files with very long lines.

Minified HTML/JS puts 100k+ characters on one line. The column mapping used
to walk the line one character at a time and was called for every syntax
span, so opening such a file froze the app and every keystroke took seconds.
"""

import random
import time

from textual.app import App, ComposeResult

from dunders.windowing.core.buffer import TextBuffer
from dunders.windowing.core.fold_engine import FoldRegion, FoldRule
from dunders.windowing.editor.widget import EditorWidget
from dunders.windowing.palette import Palette
from dunders.windowing.themes.modern_dark import modern_dark


class _Host(App):
    def __init__(self, text: str, path: str) -> None:
        super().__init__()
        self._buf = TextBuffer.from_string(text)
        self._buf.file_path = path
        self._palette = Palette(modern_dark)

    def compose(self) -> ComposeResult:
        yield EditorWidget(buffer=self._buf, palette=self._palette)


def _long_html_line(n_tags: int) -> str:
    return "<html>" + "".join(
        f'<div class="c{i}" id="x{i}"><span>text {i}</span></div>'
        for i in range(n_tags)
    ) + "</html>"


# Reference implementations: the original character-walking algorithms.

def _ref_buf_to_vis(line: str, regions: list[FoldRegion], row: int, buf_col: int) -> int:
    vis_col = 0
    pos = 0
    while pos < buf_col and pos < len(line):
        skipped = False
        for region in regions:
            if (region.collapsed and not region.is_block
                    and region.start_row == row and region.start_col == pos):
                vis_col += len(region.rule.placeholder)
                pos = region.end_col + 1
                skipped = True
                break
        if not skipped:
            pos += 1
            vis_col += 1
    return vis_col


def _ref_vis_to_buf(line: str, regions: list[FoldRegion], row: int, rendered_col: int) -> int:
    buf_col = 0
    vis_col = 0
    while buf_col < len(line) and vis_col < rendered_col:
        skipped = False
        for region in regions:
            if (region.collapsed and not region.is_block
                    and region.start_row == row and region.start_col == buf_col):
                vis_col += len(region.rule.placeholder)
                buf_col = region.end_col + 1
                skipped = True
                break
        if not skipped:
            buf_col += 1
            vis_col += 1
    return buf_col


def test_column_mapping_matches_reference_with_inline_folds():
    rng = random.Random(1234)
    rule_a = FoldRule("{", "}", "⋯")
    rule_b = FoldRule("(", ")", "⦅…⦆")
    for _ in range(200):
        length = rng.randint(0, 60)
        line = "x" * length
        regions = []
        for _ in range(rng.randint(0, 6)):
            if length < 2:
                break
            s = rng.randint(0, length - 1)
            e = rng.randint(s, min(length + 2, s + 15))
            regions.append(FoldRegion(
                0, s, 0, e, rng.choice([rule_a, rule_b]),
                collapsed=rng.random() < 0.7,
            ))
        # A block region on the row must never affect column mapping.
        regions.append(FoldRegion(0, 1, 3, 0, rule_a, collapsed=True))
        editor = EditorWidget(buffer=TextBuffer.from_string(line + "\na\nb\nc"))
        editor._fold_regions = regions
        for col in range(length + 4):
            assert editor._buffer_col_to_rendered_col(0, col) == \
                _ref_buf_to_vis(line, regions, 0, col), (line, regions, col)
            assert editor._rendered_col_to_buffer_col(0, col) == \
                _ref_vis_to_buf(line, regions, 0, col), (line, regions, col)


async def test_render_long_highlighted_line_is_fast():
    text = _long_html_line(4000) + "\nshort\n"
    assert len(text) > 150_000
    app = _Host(text, "page.html")
    async with app.run_test(size=(100, 20)) as pilot:
        editor = app.query_one(EditorWidget)
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert len(editor._syntax_spans[0]) > 10_000
        t = time.perf_counter()
        for _ in range(5):
            editor.render_line(0)
        assert time.perf_counter() - t < 1.0


async def test_long_line_renders_visible_window_after_horizontal_scroll():
    line = "".join(f"{i:07d}," for i in range(20_000))
    app = _Host(line + "\n", "data.txt")
    async with app.run_test(size=(60, 10)) as pilot:
        editor = app.query_one(EditorWidget)
        await pilot.pause()
        editor.scroll_to(x=80_000, y=0, animate=False)
        await pilot.pause()
        scroll_x = editor.scroll_offset.x
        assert scroll_x == 80_000
        strip = editor.render_line(0)
        gutter = editor._gutter_width()
        body = strip.text[gutter:]
        body_width = editor._content_width() - gutter
        assert body == line[scroll_x:scroll_x + body_width]
        assert strip.cell_length == editor._content_width()


async def test_cursor_at_eol_marker_visible_on_long_line():
    line = "a" * 50_000
    app = _Host(line + "\n", "data.txt")
    async with app.run_test(size=(60, 10)) as pilot:
        editor = app.query_one(EditorWidget)
        await pilot.pause()
        editor.focus()
        await pilot.press("end")
        await pilot.pause()
        assert editor.buffer.cursor_col == 50_000
        strip = editor.render_line(0)
        gutter = editor._gutter_width()
        body = strip.text[gutter:].rstrip()
        # The window ends with the line's tail; the EOL marker cell follows it.
        assert body and set(body) == {"a"}
        segments = list(strip._segments)
        assert segments[-1].text == " ", "past-EOL cursor marker not rendered"
        assert segments[-1].style != segments[-2].style
